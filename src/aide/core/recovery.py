"""故障分类与活性监控（D 组，error-recovery:19-28）。

看门狗做成纯判定 + 可注入中止回调，不引入线程：判定可毫秒级精确测试，
打断动作通过 abort 回调断言。用线程模拟"永不返回"要么依赖 sleep 竞态，要么逼替身支持永久阻塞。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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

    if any(key in lowered for key in ("ratelimit", "timeout", "overload", "connection", "interrupt")):
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


class StreamStrategy(str, Enum):
    """流式中断的三种恢复方式（error-recovery:46）。M1 只实现 DISCARD_RETRY。"""

    DISCARD_RETRY = "discard_retry"  # ① 丢弃半截整轮重发：最贵但最稳
    PARTIAL_AS_ASSISTANT = "partial_as_assistant"  # ② 依赖厂商原生支持，本地不可验证
    META_INSTRUCTION = "meta_instruction"  # ③ 元指令接续，拼接有风险


@dataclass(frozen=True)
class RecoveryPlan:
    action: str
    carry_partial: bool
    strategy: StreamStrategy


def decide_recovery(strategy: StreamStrategy = StreamStrategy.DISCARD_RETRY) -> RecoveryPlan:
    """决定中断后怎么恢复。②③ 在 M1 未实现，显式抛错而不是静默降级。"""
    if strategy is not StreamStrategy.DISCARD_RETRY:
        raise NotImplementedError(
            f"{strategy.value} 需要真实厂商的流式能力，M1 无法验证（见 ADR 0007）"
        )
    return RecoveryPlan("retry_whole_turn", False, strategy)


def normalize_splice(head: str, tail: str, *, scan: int = 200) -> str:
    """拼接归一化：去除重复前缀与多余空白。

    PREPARED（ADR 0007）：M1 采用策略①，本函数当前**无调用方**。
    供策略 ②③ 落地时使用；接口契约见 ADR 0007，改动需同步更新该 ADR。

    续写方式下拼接处容易多出重复字符——"参数合法不等于语义正确"（error-recovery:46）。
    这是纯函数，与是否接真实流无关，因此 M1 就能测。
    """
    head_window = head[-scan:] if scan > 0 else head
    overlap = 0
    for size in range(min(len(head_window), len(tail)), 0, -1):
        if head_window.endswith(tail[:size]):
            overlap = size
            break
    merged = head + tail[overlap:]
    return "\n".join(line.rstrip() for line in merged.split("\n")).strip()


def validate_tool_arguments_shape(arguments: dict, required: tuple[str, ...]) -> str | None:
    """只验形状与必填（存在 + 非空白），**不做语义校验**。

    PREPARED（ADR 0007）：M1 无调用方；语义校验需在 ②③ 落地时补。
    """
    for key in required:
        if key not in arguments:
            return f"缺少必填参数：{key}"
        value = arguments[key]
        if isinstance(value, str) and not value.strip():
            return f"参数 {key} 为空字符串（语法合法，语义不成立）"
    return None


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


@dataclass(frozen=True)
class FailureBreaker:
    """连续失败计数。阈值 3 来自 error-recovery:42 的生产统计。"""

    max_consecutive: int = 3
    failures: int = 0
    opened: bool = False

    def record_failure(self) -> "FailureBreaker":
        failures = self.failures + 1
        return FailureBreaker(self.max_consecutive, failures, failures >= self.max_consecutive)

    def record_success(self) -> "FailureBreaker":
        return FailureBreaker(self.max_consecutive, 0, False)


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_seconds: float
    reason: str


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """指数退避，**有硬上限**。不存在无限重试。"""
    if attempt < 0 or base <= 0 or cap <= 0:
        raise ValueError("attempt/base/cap 必须为正")
    return min(cap, base * (2**attempt))


def decide_retry(
    fault: Fault,
    attempt: int,
    *,
    max_attempts: int = 3,
    base: float = 0.5,
    cap: float = 8.0,
) -> RetryDecision:
    """只有可重试层才重试；达到上限即停，进入熔断。"""
    if not fault.retryable:
        return RetryDecision(False, 0.0, f"{fault.kind} 不可重试（{fault.layer.value} 层）")
    if attempt + 1 >= max_attempts:
        return RetryDecision(False, 0.0, f"已达最大重试次数 {max_attempts}")
    return RetryDecision(True, backoff_delay(attempt, base, cap), f"可重试：{fault.kind}")


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """熔断器：连续失败达阈值即打开，冷却后半开探测。阈值 3 来自 error-recovery:42。"""

    threshold: int = 3
    cooldown_seconds: float = 30.0
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: datetime | None = None

    def record_failure(self, now: datetime) -> BreakerState:
        self.failures += 1
        if self.state is not BreakerState.OPEN and self.failures >= self.threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now
        return self.state

    def record_success(self) -> BreakerState:
        self.failures = 0
        self.state = BreakerState.CLOSED
        self.opened_at = None
        return self.state

    def allow(self, now: datetime) -> bool:
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if self.opened_at is None:
                return False
            if (now - self.opened_at).total_seconds() >= self.cooldown_seconds:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN：放行一次探测


def tool_fingerprint(name: str, arguments: dict) -> str:
    """对「工具名 + 参数」算指纹，相同指纹反复出现即判无进展循环。"""
    payload = json.dumps(
        {"name": name, "args": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class LoopDetector:
    repeat_threshold: int = 3
    counts: dict[str, int] = field(default_factory=dict)

    def observe(self, name: str, arguments: dict) -> bool:
        """返回 True 表示已判定陷入重复调用。"""
        key = tool_fingerprint(name, arguments)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key] >= self.repeat_threshold


@dataclass(frozen=True)
class Event:
    kind: str
    detail: str


@dataclass
class EventLog:
    """接管、熔断、重试事件必须落轨迹，否则 M5 无法归因。"""

    events: list[Event] = field(default_factory=list)

    def record(self, kind: str, detail: str) -> None:
        self.events.append(Event(kind, detail))

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]


def execute_with_retry(
    provider,
    request: dict,
    *,
    strategy: StreamStrategy = StreamStrategy.DISCARD_RETRY,
    max_attempts: int = 2,
    log: EventLog | None = None,
):
    """按策略①执行：中断即**整轮重发**，请求体保持原样、不携带半截内容。"""
    decide_recovery(strategy)
    last: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            response = provider.complete(request)
            if log is not None and attempt > 0:
                log.record("retry_success", f"第 {attempt + 1} 次尝试成功")
            return response
        except BaseException as error:  # noqa: BLE001
            last = error
            fault = classify(error)
            decision = decide_retry(fault, attempt, max_attempts=max_attempts)
            if log is not None:
                log.record("retry", f"{fault.kind}: {decision.reason}")
            if not decision.retry:
                raise
    raise last  # pragma: no cover
