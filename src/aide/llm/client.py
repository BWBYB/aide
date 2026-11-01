"""Provider 客户端：**发一次请求，不重试**（ADR 0009）。

职责边界：
- 发一次 HTTP 请求、解析响应与 usage
- 把 HTTP 错误翻译成带 `retryable` / `layer` / `status_code` 的结构化异常
- **不重试、不退避、不熔断、不计数** —— 这些归 `core/recovery.py`

守卫 `test_client_has_single_request_call_site` 会数本文件里的请求调用点，
出现第二个就红。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

try:  # 离线前置必须在第三方导入之前（AGENTS.md 第七节）
    import _offline  # noqa: F401
except ImportError:  # 作为包导入时 knowledge 不在 sys.path
    pass

import httpx  # noqa: E402

from ..core.message import ToolCall  # noqa: E402


@dataclass(frozen=True)
class ProviderError(Exception):
    """结构化异常：调用方据此决定重试还是放弃（recovery.classify 消费）。"""

    message: str
    retryable: bool
    layer: str = "api"
    status_code: int | None = None

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"[{self.layer}] {self.message} (status={self.status_code}, retryable={self.retryable})"


def translate_error(status_code: int, body: str = "") -> ProviderError:
    """HTTP 语义 → 是否可重试。4xx 不可重试，429/5xx 可。

    这是纯函数，因此不需要网络就能验证「4xx 不被当可重试」。
    """
    snippet = (body or "")[:200]
    if status_code == 429 or status_code >= 500:
        return ProviderError(
            f"可重试错误：{snippet}", retryable=True, layer="api", status_code=status_code
        )
    return ProviderError(
        f"请求被拒：{snippet}", retryable=False, layer="tool", status_code=status_code
    )


@dataclass(frozen=True)
class Completion:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)


class Client:
    """OpenAI 兼容协议的薄客户端。**只有一处发起请求**。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 60.0,
        transport: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def payload(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": request.get("messages", []),
        }
        if request.get("tools"):
            payload["tools"] = request["tools"]
        return payload

    def complete(self, request: dict[str, Any]) -> Completion:
        """一次请求。失败即抛 `ProviderError`，由调用方决定是否重试。"""
        client = httpx.Client(timeout=self.timeout, transport=self._transport)
        response = client.post(  # 唯一的请求调用点
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self.payload(request),
        )
        if response.status_code >= 400:
            raise translate_error(response.status_code, response.text)
        return self._parse(response.json())

    @staticmethod
    def _parse(data: dict[str, Any]) -> Completion:
        message = (data.get("choices") or [{}])[0].get("message", {})
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(
                ToolCall(id=raw.get("id", ""), name=function.get("name", ""), arguments=arguments)
            )
        usage = data.get("usage") or {}
        return Completion(
            content=message.get("content"),
            tool_calls=tuple(calls),
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            },
        )
