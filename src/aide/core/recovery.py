"""故障分类与活性监控（D 组，error-recovery:19-28）。

设计取舍：**看门狗做成纯判定 + 可注入中止回调**，不引入线程。

理由：`test_stream_idle_watchdog_kills_stalled_stream` 要的是「连接建立但不再吐数据」
——它在时间轴上表现为「永不返回」。若用线程模拟，测试要么不可靠（sleep 竞态），
要么逼替身支持"永久阻塞"。改成判定函数后，逻辑可纯函数测（毫秒级、确定），
真正的打断动作通过 `abort` 回调断言，不需要线程。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable


class FaultLayer(str, Enum):
    """四层故障分类（error-recovery:19-24）。"""

    API = "api"  # 限流/过载/超时/连接中断：可重试
    TOOL = "tool"  # 幻觉调用/参数畸形/执行异常：喂回模型自我纠正
    CONTEXT = "context"  # 窗口溢出/压缩失败/轨迹损坏
    CONTROL = "control"  # 死循环/死亡螺旋
    UNKNOWN = "unknown"  # 未知：不得默认可重试


RETRYABLE = frozenset({FaultLayer.API})


@dataclass(frozen=True)
class Fault:
    layer: FaultLayer
    kind: str
    retryable: bool
    detail: str = ""


def classify(error: BaseException | str) -> Fault:
    """把错误归入四层之一。未知一律 `UNKNOWN` 且不可重试。"""
    name = error if isinstance(error, str) else type(error).__name__
    lowered = name.lower()

    if any(key in lowered for key in ("ratelimit", "timeout", "overload", "connection")):
        return Fault(FaultLayer.API, name, True, "基础设施噪声，可重试")
    if any(key in lowered for key in ("tool", "invalidargs", "badrequest")):
        return Fault(FaultLayer.TOOL, name, False, "工具层错误，应作为结果喂回模型")
    if any(key in lowered for key in ("context", "overflow", "compress", "orphan")):
        return Fault(FaultLayer.CONTEXT, name, False, "上下文层错误")
    if any(key in lowered for key in ("loop", "spiral", "stall")):
        return Fault(FaultLayer.CONTROL, name, False, "控制流层错误")
    return Fault(FaultLayer.UNKNOWN, name, False, "未知错误：不得默认可重试")


def should_kill(last_data_at: datetime, now: datetime, idle_timeout_seconds: float) -> bool:
    """空闲看门狗判定：多久没新数据就判死。

    纯函数，时钟由外部注入——与 `check_limits` 同一手法，可精确测试。
    """
    if idle_timeout_seconds <= 0:
        raise ValueError("idle_timeout_seconds 必须为正数")
    return (now - last_data_at).total_seconds() >= idle_timeout_seconds


@dataclass
class StreamWatchdog:
    """带中止回调的看门狗。`check()` 命中即调用一次 `abort`，重复调用不会重复中止。"""

    idle_timeout_seconds: float
    abort: Callable[[str], None]
    _killed: bool = False

    def check(self, last_data_at: datetime, now: datetime) -> bool:
        if self._killed:
            return True
        if should_kill(last_data_at, now, self.idle_timeout_seconds):
            self._killed = True
            self.abort(
                f"空闲超时：{(now - last_data_at).total_seconds():.0f}s 无新数据"
                f"（阈值 {self.idle_timeout_seconds:.0f}s）"
            )
            return True
        return False

    @property
    def killed(self) -> bool:
        return self._killed
