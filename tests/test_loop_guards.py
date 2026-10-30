"""G 组：预算、终止与验证（5 条断言）。

超时怎么测：**不真睡**。`ManualClock` 由替身在每次调用时推进 `latency_seconds`，
语义上等价于"这次调用花了 N 秒"，且完全确定。真 sleep 会让测试又慢又不稳，
而且掩盖一个事实——超时与抛错是两条不同路径：`fail_after` 注入的是立刻返回的异常。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from aide.core.context import Prefix, ToolSpec
from aide.core.loop import (
    Limits,
    LoopResult,
    RunState,
    StopReason,
    check_limits,
    run_loop,
)
from aide.core.verifier import PYTHON_FILE_BASELINE, verify
from fakes import FakeProvider, ManualClock, ModelResponse

START = datetime(2026, 10, 30, 15, 0, 0)

PREFIX = Prefix(
    system_prompt="你是严谨的工程助理。",
    tools=(ToolSpec("write_file", "写文件", {"type": "object"}),),
)


def draft_provider(**kwargs) -> FakeProvider:
    """永远提交不合格产物的替身，用来撞三重闸门。"""
    return FakeProvider([ModelResponse(content="草稿，未完成")], **kwargs)


@pytest.mark.unit
def test_turn_limit_terminates_with_reason() -> None:
    """轮次耗尽返回结构化原因，轨迹保留，不得静默返回空答案。"""
    clock = ManualClock(START)
    provider = draft_provider()
    result = run_loop(
        provider,
        PREFIX,
        "写一个脚本",
        limits=Limits(max_turns=2, max_tokens=10**9, max_seconds=10**6),
        clock=clock,
        verifier_baseline=PYTHON_FILE_BASELINE,
    )

    assert result.stop.reason is StopReason.TURN_LIMIT
    assert result.stop.is_limit
    assert "2/2" in result.stop.detail
    assert result.turns == 2
    assert result.partial, "已完成的轮次不应被丢弃"
    assert any(m.role == "assistant" for m in result.trajectory)


@pytest.mark.unit
def test_budget_limit_terminates() -> None:
    """token 预算耗尽即停，且不抛异常。"""
    clock = ManualClock(START)
    # 产物必须"不合格"（只含注释），否则第一轮就会被判完成，撞不到预算闸门
    provider = FakeProvider(
        [ModelResponse(content="# 草稿，未完成", usage={"total_tokens": 120})]
    )
    result = run_loop(
        provider,
        PREFIX,
        "写一个脚本",
        limits=Limits(max_turns=10, max_tokens=200, max_seconds=10**6),
        clock=clock,
        verifier_baseline=PYTHON_FILE_BASELINE,
    )

    assert result.stop.reason is StopReason.BUDGET_LIMIT
    assert result.tokens_used >= 200
    assert "token" in result.stop.detail
    assert isinstance(result, LoopResult)


@pytest.mark.unit
def test_timeout_limit_terminates() -> None:
    """墙钟超时生效，且已完成步骤的轨迹不丢。

    这是唯一需要"时间流逝"的断言。用注入时钟 + 替身推进 30s/次，
    而不是真的 sleep——超时是时间问题，不是异常问题。
    """
    clock = ManualClock(START)
    provider = draft_provider(clock=clock, latency_seconds=30.0)
    result = run_loop(
        provider,
        PREFIX,
        "写一个脚本",
        limits=Limits(max_turns=10, max_tokens=10**9, max_seconds=50.0),
        clock=clock,
        verifier_baseline=PYTHON_FILE_BASELINE,
    )

    assert result.stop.reason is StopReason.TIMEOUT
    assert "墙钟" in result.stop.detail
    assert result.turns == 2, "超时前应完整跑完两步"
    assert len(result.trajectory) >= 3, "已完成步骤的轨迹被丢弃"


@pytest.mark.unit
def test_completion_claim_requires_verifier() -> None:
    """模型自称完成不得直接终止，必须经 verifier 判定。"""
    clock = ManualClock(START)

    # 情形一：没有验收基线 —— 连"完成"都无法确认
    no_baseline = run_loop(
        draft_provider(),
        PREFIX,
        "写一个脚本",
        limits=Limits(max_turns=3, max_tokens=10**9, max_seconds=10**6),
        clock=clock,
        verifier_baseline=None,
    )
    assert no_baseline.stop.reason is StopReason.VERIFICATION_UNAVAILABLE

    # 情形二：第一次被判不合格 → 继续；第二次通过 → 才终止
    clock2 = ManualClock(START)
    provider = FakeProvider(
        [ModelResponse(content="   "), ModelResponse(content="print(1)\n")]
    )
    result = run_loop(
        provider,
        PREFIX,
        "写一个脚本",
        limits=Limits(max_turns=5, max_tokens=10**9, max_seconds=10**6),
        clock=clock2,
        verifier_baseline=PYTHON_FILE_BASELINE,
    )

    assert result.stop.reason is StopReason.COMPLETED
    assert result.turns == 2, "第一次自称完成时不应终止"
    assert result.verification is not None and result.verification.ok
    assert any(
        (m.content or "").startswith("验收未通过") for m in result.trajectory
    ), "应把失败原因回灌给模型"


@pytest.mark.unit
def test_verifier_rejects_obviously_bad_output() -> None:
    """空文件 / 语法错误 / 只改注释 —— 三种都必须被拒，且原因可定位。"""
    empty = verify("   ", PYTHON_FILE_BASELINE)
    assert not empty.ok
    assert "空" in empty.reason()

    broken = verify("def broken(:\n    pass\n", PYTHON_FILE_BASELINE)
    assert not broken.ok
    assert "第 1 行" in broken.reason(), f"语法错误应给出行号：{broken.reason()}"

    comment_only = verify("# 只改了一行注释\n", PYTHON_FILE_BASELINE)
    assert not comment_only.ok
    assert "注释" in comment_only.reason()

    good = verify("def run() -> int:\n    return 1\n", PYTHON_FILE_BASELINE)
    assert good.ok, f"合格产物被误判：{good.reason()}"


@pytest.mark.unit
def test_check_limits_order_is_deterministic() -> None:
    """三条闸门同时触发时，结论按固定顺序给（轮次 → 预算 → 墙钟）。"""
    state = RunState(started_at=START, turns=5, tokens_used=500)
    stop = check_limits(
        state, Limits(max_turns=5, max_tokens=100, max_seconds=1.0), START
    )
    assert stop is not None and stop.reason is StopReason.TURN_LIMIT
