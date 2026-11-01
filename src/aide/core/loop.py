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

from .compressor import CompressionConfig, compress
from .limits import Limits, LoopError
from .context import Prefix, render_request
from .message import Msg, ToolCall
from .policy import Action, Verdict, evaluate
from .state_bar import SystemState, TraceSummary, build_state_bar
from .verifier import Baseline, Verification, verify

Clock = Callable[[], datetime]


class StopReason(str, Enum):
    TURN_LIMIT = "turn_limit"
    BUDGET_LIMIT = "budget_limit"
    TIMEOUT = "timeout"
    COMPLETED = "completed"
    VERIFICATION_UNAVAILABLE = "verification_unavailable"
    CONTEXT_REFUSED = "context_refused"  # 压缩拒绝：不能带着这个上下文继续


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


@dataclass
class RunState:
    started_at: datetime
    turns: int = 0
    tokens_used: int = 0

    def elapsed(self, now: datetime) -> float:
        return (now - self.started_at).total_seconds()


def check_limits(state: RunState, limits: Limits, now: datetime) -> Stop | None:
    """三重闸门。顺序固定：轮次 → 预算 → 墙钟，保证同一状态有确定结论。

    **`max_seconds` 的适用边界**：`now` 是参数，所以本闸门只在**两次调用之间**被检查，
    防的是"多次慢调用累积超预算"。它的隐含前提是**每次调用都会返回**——
    若 provider 建立连接后再也不吐数据，`now` 不会更新，这条闸门永不触发。
    单次调用永久挂起属于 D 组的空闲看门狗（`test_stream_idle_watchdog_kills_stalled_stream`），
    两者互补，不重复。
    """
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

EXEC_TOOLS = frozenset({"exec", "shell", "bash", "python_exec"})


def _action_of(call: ToolCall) -> Action:
    """把工具调用映射成待判定动作。M2 接真实工具表时只改这里。"""
    if call.name in EXEC_TOOLS:
        return Action("exec", command=str(call.arguments.get("command", "")))
    if call.name.startswith(("write", "edit", "delete")):
        return Action(
            "write",
            path=call.arguments.get("path"),
            read_first=bool(call.arguments.get("read_first", False)),
        )
    return Action("read", path=call.arguments.get("path"))


def _run_tool(
    call: ToolCall,
    sandbox_root: str | None,
    readonly_roots: Sequence[str],
    executor: Callable[[ToolCall], str] | None,
    placeholder: str,
) -> str:
    """策略在前、执行在后：被拒的动作**不会**到达执行器。"""
    if sandbox_root is not None:
        decision = evaluate(
            _action_of(call), sandbox_root=sandbox_root, readonly_roots=readonly_roots
        )
        if decision.verdict is Verdict.DENY:
            return f"[policy] 拒绝：{decision.reason}"
        if decision.verdict is Verdict.NEED_APPROVAL:
            return f"[policy] 需要审批：{decision.reason}"
    if executor is None:
        return placeholder
    return executor(call)


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
    compression: CompressionConfig | None = None,
    sandbox_root: str | None = None,
    readonly_roots: Sequence[str] = (),
    tool_executor: Callable[[ToolCall], str] | None = None,
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

        # 压缩必须在装配之前：它作用在轨迹上，状态栏随后才追加（位置不变量）
        compressed = compress(trajectory, limits=limits, config=compression or CompressionConfig())
        if compressed.refused:
            return LoopResult(
                Stop(StopReason.CONTEXT_REFUSED, compressed.refusal_reason or "压缩拒绝"),
                tuple(trajectory),
                verification,
                state.turns,
                state.tokens_used,
            )

        summary = TraceSummary.from_trace(trajectory, system=system)
        request = render_request(prefix, compressed.messages, build_state_bar(summary, now))
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
                    Msg(role="tool", content=_run_tool(call, sandbox_root, readonly_roots, tool_executor, tool_placeholder), tool_call_id=call.id)
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
