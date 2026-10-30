"""消息与内容块 schema。

依据 `context-engineering:21`：上下文 = 四种消息角色 + `tools` 字段。
工具定义走请求顶层 `tools`，**不是消息角色**——所以这里没有 `tools` 角色，
工具调用以 `assistant.tool_calls` + `tool` 消息表达。

只用标准库：本模块要能被离线探针以最小依赖导入（见 AGENTS.md 第七节）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: 只允许这四种角色（context-engineering:21）
Role = Literal["system", "user", "assistant", "tool"]

VALID_ROLES: frozenset[str] = frozenset({"system", "user", "assistant", "tool"})


class MessageError(ValueError):
    """消息结构非法。"""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class Msg:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise MessageError(f"非法角色：{self.role!r}")
        if self.role == "tool" and not self.tool_call_id:
            raise MessageError("tool 消息必须带 tool_call_id（靠它与调用配对）")
        if self.role != "tool" and self.tool_call_id:
            raise MessageError(f"{self.role} 消息不应带 tool_call_id")
        if not self.content and not self.tool_calls:
            raise MessageError("消息必须有 content 或 tool_calls")
        if self.tool_calls and self.role != "assistant":
            raise MessageError("只有 assistant 可以携带 tool_calls")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload
