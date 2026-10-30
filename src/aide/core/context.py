"""上下文装配：纯函数。

设计约束（对应 A 组 8 条断言）：

1. **纯函数，无实例状态**。同样的输入必须产出同样的字节。一旦混进 `self._turn`
   这类状态，"前缀稳定"就变成"跑 10 轮看看一样不一样"——测的是运气。
2. **前缀 = 系统提示 + 工具定义，且工具定义不进 messages**（context-engineering:21）。
3. **状态栏是参数，不是内部生成**。它不可能出现在前缀里，装配时只能追加到尾部
   （kv-cache-design:21 铁律二）。
4. **段序固定**：前缀 → 轨迹 → 状态栏（kv-cache-design:33）。

只用标准库。见 AGENTS.md 第七节。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .message import Msg


def _freeze(value: Any) -> Any:
    """递归冻结：dict → MappingProxyType，list → tuple。

    `frozen=True` 只阻止字段重新赋值，不阻止 `tool.parameters["x"]["y"] = ...`。
    前缀缓存失效最常见的来源就是同一份工具定义被就地改了一处——所以这里冻结到叶子。
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """还原成 JSON 可序列化的普通类型。"""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value

STATE_BAR_OPEN = "<agent_status>"
STATE_BAR_CLOSE = "</agent_status>"


class ContextError(ValueError):
    """上下文装配失败。"""


@dataclass(frozen=True)
class ToolSpec:
    """工具定义。顺序即位置，不做任何排序（kv-cache-design:64,79）。

    `parameters` 在构造时被**递归冻结**：顶层与嵌套层都不可变，
    就地修改会抛 TypeError，而不是静默改变前缀字节。
    """

    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _freeze(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _thaw(self.parameters),
        }


@dataclass(frozen=True)
class Prefix:
    """静态前缀。构造后不可变，任何字段变化都会改变 digest。"""

    system_prompt: str
    tools: tuple[ToolSpec, ...] = ()

    def system_message(self) -> Msg:
        return Msg(role="system", content=self.system_prompt)

    def tools_payload(self) -> list[dict[str, Any]]:
        """顶层 `tools` 字段的内容。顺序即声明顺序。"""
        return [tool.to_dict() for tool in self.tools]

    def canonical_bytes(self) -> bytes:
        """前缀的唯一权威字节表示。缓存命中率以它为基准。

        `sort_keys=True` 只约束 **dict 键** 的顺序，不重排 tools **列表**——
        书里禁止的是后者（kv-cache-design:64,79），前者是让"同一份 schema
        由不同来源构造"也能得到同一串字节。
        """
        payload = {
            "system_prompt": self.system_prompt,
            "tools": self.tools_payload(),
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def digest(self) -> str:
        """前缀指纹。断言里比较它，而不是比较对象。"""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def wrap_state_bar(state_bar: str) -> Msg:
    """把状态栏内容包成一条尾部 user 消息。

    角色用 user、内容用 `<agent_status>` 包裹，均为协议层选择，不代表用户输入
    （agent-state-bar:63-70）。
    """
    text = state_bar.strip()
    if not text:
        raise ContextError("状态栏内容为空：没有状态可注入时不应占位")
    if STATE_BAR_OPEN in text or STATE_BAR_CLOSE in text:
        raise ContextError("状态栏内容不得自带 <agent_status> 标签")
    return Msg(role="user", content=f"{STATE_BAR_OPEN}\n{text}\n{STATE_BAR_CLOSE}")


def assemble_context(
    prefix: Prefix,
    trajectory: Sequence[Msg],
    state_bar: str,
) -> list[Msg]:
    """装配一次请求的 messages。

    纯函数：不读时钟、不读环境、不依赖调用次数。
    轨迹里不得含 system 角色——系统提示只能来自 prefix，否则前缀可被绕过。
    """
    for index, message in enumerate(trajectory):
        if message.role == "system":
            raise ContextError(
                f"轨迹第 {index} 条是 system 消息：系统提示只能来自 Prefix，"
                "否则前缀稳定性与注入防御同时失效"
            )
    return [prefix.system_message(), *trajectory, wrap_state_bar(state_bar)]


def render_request(
    prefix: Prefix,
    trajectory: Sequence[Msg],
    state_bar: str,
) -> dict[str, Any]:
    """装配完整请求体：messages 在里，tools 在顶层。"""
    return {
        "messages": [msg.to_dict() for msg in assemble_context(prefix, trajectory, state_bar)],
        "tools": prefix.tools_payload(),
    }


def message_bytes(messages: Iterable[Msg]) -> bytes:
    """把 messages 序列化成稳定字节，用于逐字节比对。"""
    payload = [msg.to_dict() for msg in messages]
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=False, separators=(",", ":")
    ).encode("utf-8")
