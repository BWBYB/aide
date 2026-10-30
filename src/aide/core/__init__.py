"""核心层：消息、上下文装配、状态栏、压缩、主循环、策略、验证器。"""

from .context import (
    Prefix,
    ToolSpec,
    assemble_context,
    message_bytes,
    render_request,
    wrap_state_bar,
)
from .message import Msg, ToolCall
from .state_bar import (
    ErrorSummary,
    StateBarError,
    SystemState,
    TodoItem,
    TraceSummary,
    build_state_bar,
    state_bar_payload,
)

__all__ = [
    "Msg",
    "ToolCall",
    "Prefix",
    "ToolSpec",
    "assemble_context",
    "render_request",
    "wrap_state_bar",
    "message_bytes",
    "TraceSummary",
    "TodoItem",
    "ErrorSummary",
    "SystemState",
    "StateBarError",
    "build_state_bar",
    "state_bar_payload",
]
