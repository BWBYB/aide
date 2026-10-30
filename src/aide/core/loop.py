"""主循环与三重闸门。

三条闸门（轮次 / token 预算 / 墙钟）都必须给**结构化终止原因**，不允许静默返回空答案。
时钟由外部注入，便于用假时钟测墙钟超时而不真的睡。

完成判定走 `verifier.verify(artifact, baseline)`：模型自称完成只是"提出"，
必须经独立验证才写 `completed`（loop-engineering:17,19）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Protocol, Sequence

from .context import Prefix, render_request
from .message import Msg
from .state_bar import SystemState, TraceSummary, build_state_bar
from .verifier import Baseline, Verification, verify

Clock = Callable[[], datetime]


class LoopError(RuntimeError):
    """循环自身的配置或状态错误（不是模型侧故障）。"""


class StopReason(str, Enum):
    TURN_LIMIT = "turn_limit"
    BUDGET_LIMIT = "budget_limit"
    TIMEOUT = "timeout"
    COMPLETED = "completed"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"


@dataclass(frozen=True)
class Stop:
    reason: StopReason
    detail: str

    @property
    def is_limit(self) -> bool:
        return self.reason in {
            StopReason.TURN_LIMIT,
            StopReason.BUDGET_LIMIT,
            StopReason.TIMEOUT,
        }


@dataclass(frozen=True)
class Limits:
    max_turns: int = 8
    max_tokens: int = 200_000
    max_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_turns <= 0 or self.max_tokens <= 0 or self.max_seconds <= 0:
            raise LoopError("三重闸门都必须为正数")


@dataclass
class RunState:
    started_at: datetime
    turns: int = 0
    tokens_used: int = 0

    def elapsed(self, now: datetime) -> float:
        return (now - self.started_at).total_seconds()


def check_limits(state: RunState, limits: Limits, now: datetime) -> Stop | None:
    """三重闸门。顺序固定：轮次 → 预算 → 墙钟，保证同一状态有确定结论。"""
    if state.turns >= limits.max_turns:
        return Stop(StopReason.TURN_LIMIT, f"轮次耗尽：{state.turns}/{limits.max_turns}")
    if state.tokens_used >= limits.max_tokens:
        return Stop(
            StopReason.BUDGET_LIMIT,
            f"token 预算耗尽：{state.tokens_used}/{limits.max_tokens}",
        )
    elapsed = state.elapsed(now)
    if elapsed >= limits.max_seconds:
        return Stop(StopReason.TIMEOUT, f"墙钟超时：{elapsed:.0f}s/{limits.max_seconds:.0f}s")
    return None


class Provider(Protocol):
    def complete(self, request: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class LoopResult:
    stop: Stop
    trajectory: tuple[Msg, ...]
    verification: Verification | None
    turns: int
    tokens_used: int

    @property
    def partial(self) -> bool:
        """预算/超时中断时仍有已完成步骤——这些轨迹不能丢。"""
        return self.turns > 0


DEFAULT_TOOL_PLACEHOLDER = "[M1 占位：工具执行在 M2 接入，此处只保证轨迹配对完整]"


def run_loop(
    provider: Provider,
    prefix: Prefix,
    task: str,
    *,
    limits: Limits,
    clock: Clock,
    verifier_baseline: Baseline | None = None,
    system: SystemState | None = None,
    tool_placeholder: str = DEFAULT_TOOL_PLACEHOLDER,
) -> LoopResult:
    """跑一次最小 ReAct 循环。工具执行属 M2，这里只回填占位结果以保持配对完整。"""
    started = clock()
    state = RunState(started_at=started)
    trajectory: list[Msg] = [Msg(role="user", content=task)]
    verification: Verification | None = None

    while True:
        now = clock()
        stop = check_limits(state, limits, now)
        if stop is not None:
            return LoopResult(stop, tuple(trajectory), verification, state.turns, state.tokens_used)

        summary = TraceSummary.from_trace(trajectory, system=system)
        request = render_request(prefix, trajectory, build_state_bar(summary, now))
        response = provider.complete(request)
        state.turns += 1
        state.tokens_used += int(getattr(response, "usage", {}).get("total_tokens", 0))

        trajectory.append(
            Msg(
                role="assistant",
                content=response.content,
                tool_calls=tuple(response.tool_calls),
            )
        )

        if response.tool_calls:
            for call in response.tool_calls:
                trajectory.append(
                    Msg(role="tool", content=tool_placeholder, tool_call_id=call.id)
                )
            continue

        # 模型提出了"完成"。这不是结论，只是申请。
        if verifier_baseline is None:
            return LoopResult(
                Stop(StopReason.VERIFICATION_UNAVAILABLE, "未配置验收基线，无法判定完成"),
                tuple(trajectory),
                None,
                state.turns,
                state.tokens_used,
            )

        verification = verify(response.content or "", verifier_baseline)
        if verification.ok:
            return LoopResult(
                Stop(StopReason.COMPLETED, f"验收通过：{verification.baseline}"),
                tuple(trajectory),
                verification,
                state.turns,
                state.tokens_used,
            )
        trajectory.append(
            Msg(
                role="user",
                content="验收未通过：" + verification.reason() + "。请修正后重新提交。",
            )
        )
