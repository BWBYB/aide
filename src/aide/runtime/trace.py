"""轨迹落盘：逐轮记录请求与响应，供 `make replay` 回放。

字段是验收清单要求的：`prefix_sha256` / 状态栏 / 工具调用 / tokens / cost。
`cost_usd` 在未配置价目表时为 `None`——**字段必须在，值可以为空**，
否则「成本可追溯」这条无法成立。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


def prefix_digest(request: dict[str, Any]) -> str:
    """从请求体反推前缀指纹（与 A 组 `prefix_bytes_of` 同一口径）。"""
    messages = request.get("messages") or [{}]
    payload = {"system_prompt": messages[0].get("content"), "tools": request.get("tools", [])}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class TraceEntry:
    turn: int
    prefix_sha256: str
    state_bar: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None


class TraceRecorder:
    """包装任意 provider，记录每次请求/响应。对调用方完全透明。"""

    def __init__(self, provider: Any, price: "PriceTable | None" = None) -> None:
        self._provider = provider
        self._entries: list[TraceEntry] = []
        self._price = price

    def complete(self, request: dict[str, Any]) -> Any:
        response = self._provider.complete(request)
        messages = request.get("messages") or []
        state_bar = ""
        for message in reversed(messages):
            content = message.get("content") or ""
            if message.get("role") == "user" and content.startswith("<agent_status>"):
                state_bar = content
                break
        usage = dict(getattr(response, "usage", {}) or {})
        self._entries.append(
            TraceEntry(
                turn=len(self._entries) + 1,
                prefix_sha256=prefix_digest(request),
                state_bar=state_bar,
                tool_calls=[
                    {"name": call.name, "arguments": call.arguments}
                    for call in getattr(response, "tool_calls", ())
                ],
                response_text=getattr(response, "content", None),
                usage=usage,
                cost_usd=self._price(usage) if self._price else None,
            )
        )
        return response

    @property
    def entries(self) -> list[TraceEntry]:
        return list(self._entries)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for entry in self._entries:
                handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return path


PriceTable = Callable[[dict[str, int]], float]


def load_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"轨迹不存在：{path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
