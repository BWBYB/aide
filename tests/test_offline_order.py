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
ENTRY_MODULES = ["ingest_v2", "eval_v2"]

PROBE = r"""
import importlib
import os
import sys

module_name = sys.argv[1]
importlib.import_module(module_name)

state = "unloaded"
if "huggingface_hub.constants" in sys.modules:
    import huggingface_hub.constants as constants
    state = "true" if constants.HF_HUB_OFFLINE else "false"

env = os.environ.get("HF_HUB_OFFLINE")
print(f"STATE={state} ENV={env}")

if state == "false":
    sys.exit(10)
if state == "unloaded" and env != "1":
    sys.exit(11)
sys.exit(0)
"""


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
    environment["PYTHONPATH"] = str(KNOWLEDGE)

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
        pytest.fail(f"{module_name}: 导入后既未加载 SDK 也未设置离线环境变量（{detail}）")
    assert completed.returncode == 0, f"{module_name}: 探针异常退出 {completed.returncode}（{detail}）"
