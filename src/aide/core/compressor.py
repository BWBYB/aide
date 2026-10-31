"""上下文压缩：五层里的四层（第 3 层不可本地实现，见 ADR 0006）。

设计要点：
- `compress()` 是**纯函数**：不读时钟、不调模型，summarizer 与 token 计数器都作参数注入
- 第 3 层（API 微压缩）依赖 provider 的 context-editing，本地不可实现，
  但**必须在 steps 里留下 skipped 记录**，否则"五层"会悄悄变成"四层"
- 系统提示与尾部状态栏**永不参与压缩**（kv-cache 前缀铁律 + 状态栏是末条独立的 user 消息）
- 拒绝分支在函数内部：若压完只剩"系统提示 + 状态栏"，返回 refused 且**不改动消息**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .context import STATE_BAR_OPEN
from .loop import Limits
from .message import Msg

COMPRESSED_MARK = "[COMPRESSED]"
DEFAULT_TOOL_OUTPUT_CHARS = 2000
SKIPPED_LAYER_REASON = "API 层微压缩依赖 provider 的 context-editing 能力，本地不可实现（ADR 0006）"


class CompressionError(ValueError):
    """压缩配置或产物不合法。"""


def count_tokens(text: str) -> int:
    """粗略 token 估算：CJK 按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。

    默认实现可整体替换（`compress(count_tokens=...)`），M5 接真实 tokenizer 时
    只换这一个函数，阈值语义不变。系数依据见 ADR 0005。
    """
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def message_tokens(messages: Sequence[Msg], counter: Callable[[str], int] = count_tokens) -> int:
    return sum(counter(message.content or "") for message in messages)


@dataclass(frozen=True)
class Summary:
    """结构化摘要。四类必须都有槽位，空也要渲染成"无"，不能省略。"""

    decisions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()

    def render(self) -> str:
        def join(items: tuple[str, ...]) -> str:
            return "；".join(items) if items else "无"

        return (
            f"决策: {join(self.decisions)}\n"
            f"约束: {join(self.constraints)}\n"
            f"失败: {join(self.failures)}\n"
            f"引用: {join(self.citations)}"
        )


@dataclass(frozen=True)
class NoisePolicy:
    drop_duplicate_tool_results: bool = True
    drop_blank_messages: bool = True


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
class CompressionStep:
    layer: str
    changed: bool
    reason: str


@dataclass(frozen=True)
class CompressionResult:
    messages: tuple[Msg, ...]
    steps: tuple[CompressionStep, ...]
    refused: bool
    refusal_reason: str | None
    tokens_before: int
    tokens_after: int

    @property
    def changed(self) -> bool:
        return any(step.changed for step in self.steps)

    def layer_names(self) -> tuple[str, ...]:
        return tuple(step.layer for step in self.steps)


def _is_state_bar(message: Msg) -> bool:
    return message.role == "user" and (message.content or "").startswith(STATE_BAR_OPEN)


def only_system_and_state_bar(messages: Sequence[Msg]) -> bool:
    """压缩产物是否已经退化到"只剩系统提示 + 状态栏"。

    这是断崖式下降的来源：状态栏是对原始上下文的有损投影，
    只留它会让任何未覆盖维度的问题直接失去依据（agent-state-bar:97）。
    """
    body = [m for m in messages if m.role != "system" and not _is_state_bar(m)]
    return len(body) == 0


def cap_tool_output(text: str, max_chars: int = DEFAULT_TOOL_OUTPUT_CHARS) -> tuple[str, bool]:
    """第 1 层：工具结果预算控制。返回 (新文本, 是否改动)。"""
    if COMPRESSED_MARK in text:
        return text, False
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    return f"{COMPRESSED_MARK} 原文 {len(text)} 字符，已截断至 {max_chars}\n{head}", True


def drop_noise(messages: Sequence[Msg], policy: NoisePolicy = NoisePolicy()) -> tuple[list[Msg], int]:
    """第 2 层：噪声直接删除，不做摘要（对噪声做摘要是纯浪费）。"""
    seen: set[str] = set()
    kept: list[Msg] = []
    dropped = 0
    for message in messages:
        text = message.content or ""
        if policy.drop_blank_messages and not text.strip() and not message.tool_calls:
            dropped += 1
            continue
        if policy.drop_duplicate_tool_results and message.role == "tool" and text in seen:
            dropped += 1
            continue
        if message.role == "tool":
            seen.add(text)
        kept.append(message)
    return kept, dropped


def archive_summarize(
    messages: Sequence[Msg],
    summarize_fn: Callable[[Sequence[Msg]], Summary | None],
    keep_recent: int = 4,
) -> tuple[list[Msg], int]:
    """第 4 层：归档式摘要，逐轮独立记录（像 git log，而不是 squash）。"""
    if keep_recent < 0:
        raise CompressionError("keep_recent 不能为负")
    cut = max(0, len(messages) - keep_recent)
    if cut == 0:
        return list(messages), 0

    archived: list[Msg] = []
    index = 0
    archived_count = 0
    while index < cut:
        chunk = list(messages[index : min(index + 2, cut)])
        summary = summarize_fn(chunk)
        if summary is not None:  # None = 这段没有值得保留的内容，直接丢弃
            content = (
                f"{COMPRESSED_MARK} 归档摘要 源消息 #{index}-#{index + len(chunk) - 1}\n"
                f"{summary.render()}"
            )
            archived.append(Msg(role="user", content=content))
        archived_count += len(chunk)
        index += len(chunk)
    return archived + list(messages[cut:]), archived_count


def full_compress(
    messages: Sequence[Msg],
    summarize_fn: Callable[[Sequence[Msg]], Summary | None],
    keep_recent: int = 2,
) -> tuple[list[Msg], int]:
    """第 5 层：全量压缩，最后手段。只保留最近若干条原文。"""
    if not messages:
        return [], 0
    tail = list(messages[-keep_recent:]) if keep_recent > 0 else []
    head = list(messages[: max(0, len(messages) - len(tail))])
    if not head:
        return tail, 0
    summary = summarize_fn(head)
    if summary is None:
        return tail, len(head)
    content = f"{COMPRESSED_MARK} 全量压缩 源消息 #0-#{len(head) - 1}\n{summary.render()}"
    return [Msg(role="user", content=content)] + tail, len(head)


def compress(
    messages: Sequence[Msg],
    *,
    limits: Limits,
    count: Callable[[str], int] = count_tokens,
    summarize_fn: Callable[[Sequence[Msg]], Summary | None] | None = None,
    policy: NoisePolicy = NoisePolicy(),
    max_tool_output_chars: int = DEFAULT_TOOL_OUTPUT_CHARS,
    keep_recent: int = 4,
) -> CompressionResult:
    """按五层由轻到重压缩，直到低于阈值。纯函数。"""
    threshold = limits.compression_threshold()
    before = message_tokens(messages, count)
    steps: list[CompressionStep] = []

    system = [m for m in messages if m.role == "system"]
    state_bar = [m for m in messages if _is_state_bar(m)]
    body = [m for m in messages if m.role != "system" and not _is_state_bar(m)]

    def rebuild(current: list[Msg]) -> tuple[Msg, ...]:
        return (*system, *current, *state_bar)

    if before <= threshold:
        steps.append(CompressionStep("trigger", False, f"未达阈值：{before} <= {threshold}"))
        return CompressionResult(tuple(messages), tuple(steps), False, None, before, before)

    steps.append(CompressionStep("trigger", False, f"达到阈值：{before} > {threshold}"))

    def settle(candidate: list[Msg], name: str, changed: bool, reason: str) -> None:
        """只有真的变小才接受。

        压缩做了反而变大是真实存在的（对噪声做摘要只是浪费 token，compress:69），
        因此每一层都必须过这道闸，否则"压缩"会把上下文越压越长。
        """
        nonlocal body
        if not changed:
            steps.append(CompressionStep(name, False, reason))
            return
        if message_tokens(rebuild(candidate), count) >= message_tokens(rebuild(body), count):
            steps.append(CompressionStep(name, False, f"{reason}，但压缩后未变小，已回退"))
            return
        body = candidate
        steps.append(CompressionStep(name, True, reason))

    # 第 1 层：工具结果预算控制
    capped, layer1_changed = [], False
    for message in body:
        if message.role == "tool":
            new_text, changed = cap_tool_output(message.content or "", max_tool_output_chars)
            if changed:
                layer1_changed = True
                capped.append(Msg(role="tool", content=new_text, tool_call_id=message.tool_call_id))
                continue
        capped.append(message)
    settle(capped, "cap_tool_output", layer1_changed, "截断超长工具结果并打标")

    # 第 2 层：噪声直接删除
    if message_tokens(rebuild(body), count) > threshold:
        dropped_body, dropped = drop_noise(body, policy)
        settle(dropped_body, "drop_noise", dropped > 0, f"删除 {dropped} 条噪声")

    # 第 3 层：不可用，但必须留痕
    steps.append(CompressionStep("api_micro_compression", False, SKIPPED_LAYER_REASON))

    # 第 4 层：归档式摘要
    if message_tokens(rebuild(body), count) > threshold:
        if summarize_fn is None:
            steps.append(CompressionStep("archive_summarize", False, "未注入 summarizer，跳过"))
        else:
            archived_body, archived = archive_summarize(body, summarize_fn, keep_recent)
            settle(archived_body, "archive_summarize", archived > 0, f"归档 {archived} 条消息")

    # 第 5 层：全量压缩
    if message_tokens(rebuild(body), count) > threshold:
        if summarize_fn is None:
            steps.append(CompressionStep("full_compress", False, "未注入 summarizer，跳过"))
        else:
            compressed_body, compressed = full_compress(body, summarize_fn, keep_recent=2)
            settle(compressed_body, "full_compress", compressed > 0, f"全量压缩 {compressed} 条")

    result = rebuild(body)
    after = message_tokens(result, count)

    if only_system_and_state_bar(result):
        reason = "压缩后只剩系统提示与状态栏：拒绝执行（状态栏是有损投影，会断崖式降低准确率）"
        return CompressionResult(tuple(messages), tuple(steps), True, reason, before, before)

    return CompressionResult(result, tuple(steps), False, None, before, after)
