"""接线断言：C 组（压缩）与 E 组（策略）必须真的在 run_loop 里生效。

为什么单独建文件：A–G 各组验的是**零件**，这里验的是**装配体**。
零件全绿但装配体没跑过，就是 M0 那个「切片模拟压缩」的放大版。
"""

from __future__ import annotations

import json

import pytest

from aide.core.compressor import CompressionConfig, Summary, count_tokens
from aide.core.context import Prefix, ToolSpec
from aide.core.loop import Limits, StopReason, run_loop
from aide.core.message import ToolCall
from aide.core.verifier import PYTHON_FILE_BASELINE
from fakes import FakeProvider, ManualClock, ModelResponse
from datetime import datetime

NOW = datetime(2026, 10, 31, 12, 0, 0)

PREFIX = Prefix(
    system_prompt="你是助理。",
    tools=(ToolSpec("write_file", "写文件", {"type": "object"}),),
)


def limits(window: int) -> Limits:
    return Limits(max_turns=4, max_tokens=10**7, max_seconds=10**6, context_window=window)


@pytest.mark.unit
def test_loop_compresses_when_over_threshold() -> None:
    """超阈值时 run_loop 必须真的调用压缩，而不是把原样内容发出去。"""
    long_output = "工具输出" * 1200  # 约 4800 token，远超窗口
    provider = FakeProvider(
        [
            ModelResponse(content=None, tool_calls=(ToolCall("c1", "read_file", {"path": "a"}),)),
            ModelResponse(content="print(1)\n"),
        ]
    )

    result = run_loop(
        provider,
        PREFIX,
        "读一个很大的文件",
        limits=limits(1_000),  # 阈值 800
        clock=ManualClock(NOW),
        tool_executor=lambda call: long_output,
        compression=CompressionConfig(max_tool_output_chars=200),
        verifier_baseline=PYTHON_FILE_BASELINE,
    )

    assert result.stop.reason is StopReason.COMPLETED
    second = json.dumps(provider.recorded[1], ensure_ascii=False)
    assert "[COMPRESSED]" in second, "第二轮请求里没有压缩标记，说明压缩没生效"
    second_tokens = sum(
        count_tokens(message.get("content") or "") for message in provider.recorded[1]["messages"]
    )
    assert second_tokens < 4_800, "超长工具结果被原样发了出去"


@pytest.mark.unit
def test_loop_routes_exec_through_policy() -> None:
    """exec 动作必须先过 policy；被拒时**工具不会被执行**。

    观测点是「执行器没被调用」，不是「policy 返回了 DENY」——
    后者 E 组已经验过，断言内部返回码证明不了工具真的被拦住。
    """
    calls: list[ToolCall] = []
    provider = FakeProvider(
        [
            ModelResponse(content=None, tool_calls=(ToolCall("c1", "exec", {"command": "rm -rf /"}),)),
            ModelResponse(content="print(1)\n"),
        ]
    )

    result = run_loop(
        provider,
        PREFIX,
        "清理目录",
        limits=limits(100_000),
        clock=ManualClock(NOW),
        sandbox_root="/sandbox",
        tool_executor=lambda call: calls.append(call) or "done",
    )

    assert calls == [], "被 policy 拒绝的动作仍然到达了执行器"
    tool_messages = [m for m in result.trajectory if m.role == "tool"]
    assert any("[policy] 拒绝" in (m.content or "") for m in tool_messages)


@pytest.mark.unit
def test_end_to_end_react_cycle() -> None:
    """完整一轮：请求 → 工具调用 → 结果回灌 → 验证 → 完成。"""
    executed: list[str] = []
    provider = FakeProvider(
        [
            ModelResponse(content=None, tool_calls=(ToolCall("c1", "write_file", {"path": "/sandbox/a.py", "read_first": True}),)),
            ModelResponse(content="print(1)\n"),
        ]
    )

    result = run_loop(
        provider,
        PREFIX,
        "写一个脚本",
        limits=limits(100_000),
        clock=ManualClock(NOW),
        sandbox_root="/sandbox",
        tool_executor=lambda call: executed.append(call.name) or "ok",
        verifier_baseline=PYTHON_FILE_BASELINE,
    )

    assert result.turns == 2
    assert result.stop.reason is StopReason.COMPLETED
    assert executed == ["write_file"], "sandbox 内的合法动作应被执行"
    roles = [message.role for message in result.trajectory]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert result.verification is not None and result.verification.ok
