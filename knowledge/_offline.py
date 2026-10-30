"""默认离线运行：必须在任何 transformers / huggingface_hub 导入之前生效。

为什么单独成模块：`hf_hub` 在**导入期**读取 `HF_HUB_OFFLINE` 常量。若在函数里才设，
等第三方库已经被导入就来不及了，而且失败是静默的——不报错，只是慢（每次尝试联网超时）。

用法（硬约束，见 AGENTS.md 第六节之后那条）：
    import knowledge._offline  # noqa: F401  —— 必须排在任何第三方导入之前

或者经由 `knowledge.loader` 间接生效（loader 顶部第一件事就是导入本模块）。
"""

from __future__ import annotations

import os


def apply() -> None:
    """幂等：只在用户未显式允许联网时设置离线变量。"""
    if os.environ.get("AIDE_ALLOW_HF_NETWORK"):
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


apply()

#: 供验收断言使用：入口模块应当能读到这两个变量
REQUIRED_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
