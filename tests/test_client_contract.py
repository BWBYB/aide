"""M1b 守卫与断言：client 只有一个请求点、错误分类正确、usage 进轨迹。

职责边界见 ADR 0009。守卫按"请求调用点数量"判定而不是黑名单——
黑名单永远不完备（`for _ in range(3)`、`itertools.repeat`、递归都能绕过），
而"重试"本质上就是"同一请求发多次"，数量才是判据。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from aide.llm.client import Client, translate_error
from aide.runtime.pricing import load_prices
from aide.runtime.trace import TraceRecorder

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LLM_DIR = REPO_ROOT / "src" / "aide" / "llm"
CLIENT_FILES = sorted(p for p in LLM_DIR.rglob("*.py") if p.name != "__init__.py") if LLM_DIR.is_dir() else []

REQUEST_CALLS = {"create", "post", "request", "complete", "chat", "send"}

pytestmark = pytest.mark.skipif(
    not CLIENT_FILES,
    reason="src/aide/llm 下没有实现文件（M1b 未开工）；实现后本守卫自动生效",
)


@pytest.mark.unit
def test_client_has_single_request_call_site() -> None:
    """client 里发起请求的调用点必须恰好 1 个；第二个就说明它自己实现了重试。"""
    assert CLIENT_FILES, "扫到 0 个文件：路径可能写错，守卫未生效"
    for path in CLIENT_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in REQUEST_CALLS
        ]
        assert len(hits) == 1, f"{path.name} 有 {len(hits)} 个请求调用点，重试/退避应属 recovery.py"


@pytest.mark.unit
def test_provider_classifies_4xx_as_non_retryable() -> None:
    """4xx 不可重试；429/5xx 可重试。"""
    assert translate_error(400, "bad request").retryable is False
    assert translate_error(403, "forbidden").retryable is False
    assert translate_error(429, "slow down").retryable is True
    assert translate_error(503, "unavailable").retryable is True
    assert translate_error(400).status_code == 400


@pytest.mark.unit
def test_provider_parses_usage_into_trace() -> None:
    """真实响应的 usage 被解析，并随 model / price_version 落进轨迹。"""
    payload = {
        "choices": [
            {
                "message": {
                    "content": "好的",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "a.md"}'}}
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    completion = Client._parse(payload)
    assert completion.usage["total_tokens"] == 15
    assert completion.tool_calls[0].arguments == {"path": "a.md"}, "工具参数未从 JSON 解析"

    book = load_prices(REPO_ROOT / "configs" / "prices.json")
    recorder = TraceRecorder(
        type("P", (), {"complete": lambda self, req: completion})(),
        price=lambda usage: book.cost("deepseek-v4", usage),
        model="deepseek-v4",
        price_version=book.version,
    )
    recorder.complete({"messages": [{"role": "user", "content": "hi"}], "tools": []})

    entry = recorder.entries[0]
    assert entry.usage["total_tokens"] == 15
    assert entry.model == "deepseek-v4"
    assert entry.price_version == book.version
    assert entry.cost_usd is not None and entry.cost_usd > 0
