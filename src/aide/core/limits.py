"""运行上限配置。

单独成文件的原因：它原本定义在 `loop.py`，而 `compressor.py` 需要它，
于是形成 `loop → compressor → loop` 的循环依赖，之前靠 `TYPE_CHECKING`
加鸭子类型绕过——运行时安全，但依赖图里的环还在。

`Limits` 本来就是一份配置，不属于循环，抽出来环就消失了。
"""

from __future__ import annotations

from dataclasses import dataclass


class LoopError(RuntimeError):
    """循环自身的配置或状态错误（不是模型侧故障）。"""


@dataclass(frozen=True)
class Limits:
    """运行上限。

    两个 token 相关的字段语义不同，**不可混用**：
    - `max_tokens`：跨轮次**累计预算**，撞到即终止整个运行；
    - `context_window`：**单次请求**的窗口大小，压缩阈值按它算（80%）。

    混用会让压缩在错误的位置触发：预算耗尽与上下文溢出是两类故障
    （error-recovery:23 把上下文层单列）。
    """

    max_turns: int = 8
    max_tokens: int = 200_000
    max_seconds: float = 300.0
    context_window: int = 128_000

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tokens", "max_seconds", "context_window"):
            if getattr(self, name) <= 0:
                raise LoopError(f"{name} 必须为正数")

    def compression_threshold(self, ratio: float = 0.8) -> int:
        """压缩触发点：单次请求窗口的 80%（compress:49）。"""
        if not 0 < ratio < 1:
            raise LoopError("压缩阈值比例必须在 (0, 1) 之间")
        return int(self.context_window * ratio)
