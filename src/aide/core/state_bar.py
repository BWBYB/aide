"""状态栏：把隐式状态提炼为显式知识。

依据 `agent-state-bar:82-88` 的五种技术：时间戳 / 工具调用计数器 / TODO 列表 /
详细错误信息 / 系统状态感知。依据 `agent-state-bar:95` 的硬约束：
**计数必须由代码维护，不能由模型自报**——"你写打了 3 次电话，它就当真是 3 次"。

所以本模块的核心是 `TraceSummary.from_trace()`：只扫 `assistant.tool_calls` 计数，
**完全不读 `content`**。模型在正文里说"我调用了 3 次"不会影响任何数字。

时钟与轨迹都作为参数传入，本模块不读系统时间、不持有状态。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from .message import Msg

#: 状态栏被模型高度信任，因此自由文本一律截断并去掉可能破坏结构的字符
MAX_FIELD_CHARS = 120
MAX_TODO_ITEMS = 8

TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")


class StateBarError(ValueError):
    """状态栏构造失败。"""


@dataclass(frozen=True)
class TodoItem:
    id: str
    text: str
    status: str = "pending"

    def __post_init__(self) -> None:
        if self.status not in TODO_STATUSES:
            raise StateBarError(f"非法 TODO 状态：{self.status!r}")


@dataclass(frozen=True)
class ErrorSummary:
    """最近一次错误的四层结构（agent-state-bar:87）。"""

    kind: str
    message: str
    tool: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class SystemState:
    """系统状态感知（agent-state-bar:88）：工作目录、OS、Shell、Python 版本。"""

    cwd: str
    os_name: str
    shell: str
    python_version: str


@dataclass(frozen=True)
class TraceSummary:
    """状态栏的输入。所有计数都来自轨迹扫描，不来自模型叙述。"""

    tool_calls: Mapping[str, int]
    todos: Sequence[TodoItem] = field(default_factory=tuple)
    last_error: ErrorSummary | None = None
    system: SystemState | None = None

    @classmethod
    def from_trace(
        cls,
        messages: Sequence[Msg],
        *,
        todos: Sequence[TodoItem] = (),
        last_error: ErrorSummary | None = None,
        system: SystemState | None = None,
    ) -> "TraceSummary":
        """从轨迹扫描出工具调用计数。

        只统计 `assistant.tool_calls`——那是框架实际执行的调用。
        `tool` 消息是结果、`content` 是模型的自然语言，两者都不参与计数。
        """
        counter: Counter[str] = Counter()
        pending: dict[str, str] = {}
        for message in messages:
            if message.role == "assistant":
                for call in message.tool_calls:
                    counter[call.name] += 1
                    pending[call.id] = call.name
            elif message.role == "tool":
                if not message.tool_call_id or message.tool_call_id not in pending:
                    raise StateBarError(
                        "轨迹完整性异常：存在没有配对 assistant 调用的 tool 结果"
                    )
                pending.pop(message.tool_call_id, None)
        return cls(
            tool_calls=dict(counter),
            todos=tuple(todos),
            last_error=last_error,
            system=system,
        )

    @property
    def total_tool_calls(self) -> int:
        return sum(self.tool_calls.values())


_TAG_RE = re.compile(r"[<>]")
_WS_RE = re.compile(r"\s+")


def _clean(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    """自由文本清洗：去标签字符、压空白、截断。

    目的有二：① 不让外部内容用 `</agent_status>` 之类字符破坏结构；
    ② 状态栏被模型高度信任，来自网页/工具结果的长文本不得原样进入（投毒面）。
    """
    collapsed = _WS_RE.sub(" ", _TAG_RE.sub("", text)).strip()
    if len(collapsed) > limit:
        return collapsed[: limit - 1] + "…"
    return collapsed


def build_state_bar(summary: TraceSummary, now: datetime) -> str:
    """生成状态栏正文（不含 `<agent_status>` 包裹，包裹由装配层负责）。

    五项固定字段，缺项以 `none` 占位而不是省略——省略会让模型无从判断
    "没有错误"与"没告诉我有没有错误"。
    """
    if not isinstance(now, datetime):
        raise StateBarError("clock 必须显式传入 datetime，不允许隐式读系统时间")

    lines: list[str] = [f"time: {now.isoformat(timespec='seconds')}"]

    if summary.tool_calls:
        counts = ", ".join(
            f"{name}={count}" for name, count in sorted(summary.tool_calls.items())
        )
    else:
        counts = "none"
    lines.append(f"tool_calls: {counts}")

    if summary.todos:
        rendered = "; ".join(
            f"[{item.id}] {_clean(item.text)} ({item.status})"
            for item in list(summary.todos)[:MAX_TODO_ITEMS]
        )
        lines.append(f"todo: {rendered}")
    else:
        lines.append("todo: none")

    if summary.last_error is None:
        lines.append("last_error: none")
    else:
        error = summary.last_error
        parts = [f"kind={_clean(error.kind)}"]
        if error.tool:
            parts.append(f"tool={_clean(error.tool)}")
        parts.append(f"msg={_clean(error.message)}")
        if error.hint:
            parts.append(f"hint={_clean(error.hint)}")
        lines.append("last_error: " + " ".join(parts))

    if summary.system is None:
        lines.append("system: none")
    else:
        system = summary.system
        lines.append(
            "system: "
            + " ".join(
                [
                    f"cwd={_clean(system.cwd)}",
                    f"os={_clean(system.os_name)}",
                    f"shell={_clean(system.shell)}",
                    f"python={_clean(system.python_version)}",
                ]
            )
        )

    return "\n".join(lines)


def state_bar_payload(summary: TraceSummary, now: datetime) -> dict[str, Any]:
    """结构化视图，供断言与 UI 使用；与正文同源，避免两处口径。"""
    return {
        "time": now.isoformat(timespec="seconds"),
        "tool_calls": dict(summary.tool_calls),
        "todo": [
            {"id": item.id, "text": _clean(item.text), "status": item.status}
            for item in summary.todos
        ],
        "last_error": None
        if summary.last_error is None
        else {
            "kind": _clean(summary.last_error.kind),
            "message": _clean(summary.last_error.message),
            "tool": _clean(summary.last_error.tool) if summary.last_error.tool else None,
            "hint": _clean(summary.last_error.hint) if summary.last_error.hint else None,
        },
        "system": None
        if summary.system is None
        else {
            "cwd": _clean(summary.system.cwd),
            "os": _clean(summary.system.os_name),
            "shell": _clean(summary.system.shell),
            "python": _clean(summary.system.python_version),
        },
    }
