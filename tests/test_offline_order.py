"""离线前置的 import 时序守卫（子进程 + 常量版）。

为什么必须用子进程：
    进程内导入顺序由测试文件自己决定——测试先 import loader 再断言，
    无论真实入口模块的顺序对不对都会通过。只有在一个**干净解释器**里按入口模块
    自己的导入顺序执行，才能复现"先 import SDK、后 import loader"的坏顺序。

为什么必须看常量而不是环境变量：
    `huggingface_hub` 在**导入期**把 `HF_HUB_OFFLINE` 固化进 `constants`。
    如果 SDK 先被导入，之后再设环境变量，`os.environ` 是对的、`constants` 是错的——
    只看环境变量的断言会漏判，而这正是需要拦住的情况。

退出码（探针子进程）：
    0  = 正常（SDK 未加载但环境已设好 / SDK 已加载且常量为 True）
    10 = SDK 已加载，但 HF_HUB_OFFLINE 常量为 False —— 设置晚了
    11 = SDK 未加载，且环境变量未设置 —— 后续一旦导入就会联网
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = REPO_ROOT / "knowledge"

#: 入口模块清单。**每新增一个会直接 import 第三方 SDK 的入口，都要加到这里。**
#: M1 必须追加 `aide.llm.client`、`aide.tools.mcp_client`（见 AGENTS.md 第七节）。
#: 清单故意从严：`src/` 下所有模块都要登记，而不是只登记"看起来会 import SDK"的那些——
#: 判断"是否需要离线"本身就容易漏，交给机器拦截更便宜。
ENTRY_MODULES = [
    "ingest_v2",
    "eval_v2",
    "aide.core.message",
    "aide.core.context",
    "aide.core.state_bar",
    "aide.core.verifier",
    "aide.core.loop",
    "aide.core.compressor",
    "aide.core.recovery",
    "aide.core.failover",
    "aide.core.policy",
]

PROBE = r"""
import importlib
import os
import sys

#: 会触发网络或读取 HF 离线常量的第三方库
SDK_ROOTS = {"openai", "httpx", "transformers", "torch", "huggingface_hub"}

module_name = sys.argv[1]
before = set(sys.modules)
importlib.import_module(module_name)
loaded = {name.split(".")[0] for name in sys.modules} - {name.split(".")[0] for name in before}

sdk = sorted(loaded & SDK_ROOTS)
state = "unloaded"
if "huggingface_hub.constants" in sys.modules:
    import huggingface_hub.constants as constants
    state = "true" if constants.HF_HUB_OFFLINE else "false"

env = os.environ.get("HF_HUB_OFFLINE")
print(f"SDK={sdk} STATE={state} ENV={env}")

# 纯标准库模块不涉及离线，无需判定
if not sdk:
    sys.exit(0)
# SDK 已导入但 HF 常量被固化为 False —— 离线设置晚了
if state == "false":
    sys.exit(10)
# 导入了 SDK，却从未应用离线策略
if env != "1":
    sys.exit(11)
sys.exit(0)
"""


def test_all_entry_modules_registered() -> None:
    """`src/` 下新增任何模块文件，都必须出现在 ENTRY_MODULES 里。

    把"别忘登记"从人的记忆变成机器拦截：M1 新建 `src/aide/llm/client.py` 的那一刻
    这条测试就红，而不是等某天它静默地先 import 了 openai。
    """
    src = REPO_ROOT / "src"
    discovered: set[str] = set()
    if src.is_dir():
        for path in src.rglob("*.py"):
            relative = path.relative_to(src).with_suffix("")
            if relative.name == "__init__":
                continue
            discovered.add(".".join(relative.parts))
    discovered |= {"ingest_v2", "eval_v2"}

    missing = discovered - set(ENTRY_MODULES)
    assert not missing, (
        f"未登记入口模块：{sorted(missing)}\n"
        "请加入 tests/test_offline_order.py 的 ENTRY_MODULES，"
        "并确认该模块在导入第三方 SDK 之前先 import knowledge._offline"
    )


@pytest.mark.skipif(
    bool(os.environ.get("AIDE_ALLOW_HF_NETWORK")),
    reason="显式允许联网时不做离线断言",
)
@pytest.mark.parametrize("module_name", ENTRY_MODULES)
def test_offline_applied_before_sdk_import(module_name: str) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AIDE_ALLOW_HF_NETWORK", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}
    }
    environment["PYTHONPATH"] = os.pathsep.join([str(KNOWLEDGE), str(REPO_ROOT / "src")])

    completed = subprocess.run(
        [sys.executable, "-c", PROBE, module_name],
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        text=True,
    )

    detail = f"stdout={completed.stdout.strip()} stderr={completed.stderr.strip()[-400:]}"
    if completed.returncode == 10:
        pytest.fail(
            f"{module_name}: 第三方 SDK 在离线设置之前就被导入，"
            f"HF_HUB_OFFLINE 常量已固化为 False（{detail}）"
        )
    if completed.returncode == 11:
        pytest.fail(
            f"{module_name}: 导入了第三方 SDK，但从未应用离线策略"
            f"（应在导入 SDK 之前 import knowledge._offline）（{detail}）"
        )
    assert completed.returncode == 0, f"{module_name}: 探针异常退出 {completed.returncode}（{detail}）"
