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
    LoopError,
    LoopResult,
    RunState,
    StopReason,
    check_limits,
    run_loop,
)
from aide.core.verifier import (
    PYTHON_FILE_BASELINE,
    extract_candidates,
    extract_code,
    verify,
    verify_all,
)
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
    # 判别力修复：旧文案让「注释」出现在**所有**被拒产物的原因里（"注释与裸表达式…"），
    # 于是 `"注释" in reason` 恒真。改断言关键词「可执行语句」，只加强不放松。
    assert "可执行语句" in comment_only.reason()

    good = verify("def run() -> int:\n    return 1\n", PYTHON_FILE_BASELINE)
    assert good.ok, f"合格产物被误判：{good.reason()}"


@pytest.mark.unit
def test_verify_all_without_baselines_is_not_pass() -> None:
    """空基线不得默认通过。

    `verify_all(x, [])` 的循环体不执行 → failures 为空 → 原本会返回 ok=True，
    与 `verify(x, None)` 走 VERIFICATION_UNAVAILABLE 的语义相反。
    这是"空实现能过"的变体：没有验收标准时，"通过"没有依据。
    """
    result = verify_all("print(1)\n", [])
    assert not result.ok
    assert result.baseline == "<none>"
    assert any("没有可用的验收基线" in item for item in result.failures)


@pytest.mark.unit
def test_limits_separates_budget_from_context_window() -> None:
    """累计预算与单次窗口是两个字段，压缩阈值只由后者决定。"""
    limits = Limits(max_turns=5, max_tokens=200_000, max_seconds=60, context_window=8_000)
    assert limits.compression_threshold() == 6_400, "阈值应按单次窗口 80% 计算"

    # 只改累计预算，不应影响压缩阈值（否则两者已被混用）
    other = Limits(max_turns=5, max_tokens=1_000_000, max_seconds=60, context_window=8_000)
    assert other.compression_threshold() == limits.compression_threshold()

    with pytest.raises(LoopError):
        Limits(context_window=0)


@pytest.mark.unit
def test_check_limits_order_is_deterministic() -> None:
    """三条闸门同时触发时，结论按固定顺序给（轮次 → 预算 → 墙钟）。"""
    state = RunState(started_at=START, turns=5, tokens_used=500)
    stop = check_limits(
        state, Limits(max_turns=5, max_tokens=100, max_seconds=1.0), START
    )
    assert stop is not None and stop.reason is StopReason.TURN_LIMIT


@pytest.mark.unit
def test_baseline_rejects_bare_identifier() -> None:
    """裸标识符不算产出：模型回一句「好」不得被判成完成。"""
    for artifact in ("好", "x", "完成"):
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"{artifact!r} 被判为通过"
        assert result.reason(), f"{artifact!r} 失败但没有给出原因"


@pytest.mark.unit
def test_baseline_rejects_bare_expression() -> None:
    """裸表达式同样不算：常量、运算、列表都没有副作用。"""
    for artifact in ("1 + 1", "[1, 2]", "'abc'"):
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"{artifact!r} 被判为通过"


@pytest.mark.unit
def test_baseline_accepts_call_and_statements() -> None:
    """配对断言：收紧判据之后，「该过的」必须还能过。

    只收紧不测这一侧，等于把基线改成永远拒绝。
    """
    for artifact in ("print(1)", "x = 1", "def f():\n    pass\n"):
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert result.ok, f"{artifact!r} 被误判：{result.reason()}"


@pytest.mark.unit
def test_baseline_accepts_fenced_code_block() -> None:
    """真实模型常用围栏包裹代码；带语言标记与不带都要能过。"""
    for artifact in ("```python\nprint(1)\n```", "```\nx = 1\n```"):
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert result.ok, f"围栏代码被误判：{artifact!r} -> {result.reason()}"


@pytest.mark.unit
def test_baseline_accepts_code_with_surrounding_prose() -> None:
    """前置说明、尾随说明、围栏夹在散文里，三种都要能过。"""
    artifacts = (
        "好的，脚本如下：\nprint(1)\n",
        "print(1)\n以上就是脚本。\n",
        "说明：\n```python\nprint(1)\n```\n完",
    )
    for artifact in artifacts:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert result.ok, f"带说明的代码被误判：{artifact!r} -> {result.reason()}"


@pytest.mark.unit
def test_extraction_does_not_weaken_baseline() -> None:
    """抽取只负责找代码，不负责判通过。

    没有这条，抽取很容易变成"只要能从文本里捞出一段能解析的东西就算过"，
    基线等于没了——和上轮的配对断言是同一手法。
    """
    artifacts = ("```\n好\n```", "```\n1 + 1\n```", "好的\n1 + 1", "```\n# 只有注释\n```")
    for artifact in artifacts:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"抽取把基线削弱了：{artifact!r} 被判通过"
        assert result.reason(), f"{artifact!r} 被拒但没给原因"


@pytest.mark.unit
def test_baseline_accepts_python_fence_after_other_block() -> None:
    """多围栏：前面是 diff / 说明块时，后面的代码块仍要被找到。"""
    artifacts = (
        "```diff\n- a\n+ b\n```\n```python\nprint(1)\n```",
        "```\n说明\n```\n```\nprint(1)\n```",
    )
    for artifact in artifacts:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert result.ok, f"多围栏被误判：{artifact!r} -> {result.reason()}"


@pytest.mark.unit
def test_baseline_accepts_indented_fence() -> None:
    """整段围栏被缩进时，先 dedent 再解析。"""
    result = verify("  ```python\n  print(1)\n  ```", PYTHON_FILE_BASELINE)
    assert result.ok, f"缩进围栏被误判：{result.reason()}"


@pytest.mark.unit
def test_baseline_accepts_prose_on_both_sides() -> None:
    """双侧散文：先剥头、在剥头的结果上再剥尾。"""
    result = verify("好的，代码如下：\nprint(1)\n需要我调整吗？", PYTHON_FILE_BASELINE)
    assert result.ok, f"双侧散文被误判：{result.reason()}"


@pytest.mark.unit
def test_baseline_rejects_when_no_candidate_has_code() -> None:
    """核心钩子：多候选 + 任一谓词满足 ≠ 有代码就能过。

    没有这条，多候选会被放宽成"这段话里随便有个能解析的片段就算完成"。
    """
    artifacts = ("```\n说明\n```", "```\n好\n```", "好的\n1 + 1")
    for artifact in artifacts:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"多候选把基线放宽了：{artifact!r} 被判通过"
        assert result.reason(), f"{artifact!r} 被拒但没给原因"


@pytest.mark.unit
def test_baseline_rejects_broken_code_between_valid_lines() -> None:
    """语法错误的代码行不可剥：旁边有合法代码也不能把它救活。

    这是"假完成"的来源——误拒只让循环空转，误放会把坏产物标成 completed。
    """
    cases = (
        ("import os\ndef f(:", "第 2 行"),
        ("def f(:\nprint(1)", "第 1 行"),
        ("x = 1\ndef f(:\ny = 2", "第 2 行"),
    )
    for artifact, expected in cases:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"{artifact!r} 被剥离救活了"
        assert expected in result.reason(), f"{artifact!r} 行号不对：{result.reason()}"


@pytest.mark.unit
def test_syntax_error_reports_original_line_number() -> None:
    """剥头用等量空行占位，报错行号必须是原文行号。"""
    cases = (("好的\nprint(1\n", "第 2 行"), ("```python\nprint(1\n```", "第 2 行"))
    for artifact, expected in cases:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"{artifact!r} 被判通过"
        assert expected in result.reason(), f"{artifact!r} 行号偏移：{result.reason()}"


@pytest.mark.unit
def test_prose_peeling_stops_at_code_line() -> None:
    """遇到第一个非散文行就停，不允许跳过它继续往下剥。"""
    blocked = verify("好的\nx = 1\ndef f(:", PYTHON_FILE_BASELINE)
    assert not blocked.ok, "代码行之后的坏代码被救活了"

    allowed = verify("好的\nprint(1)", PYTHON_FILE_BASELINE)
    assert allowed.ok, f"散文在前、代码在后应通过：{allowed.reason()}"


@pytest.mark.unit
def test_full_width_punctuation_is_peelable() -> None:
    """禁止符号表只放 ASCII：全角「：，（）」不得挡住剥离，否则中文场景全误拒。"""
    for artifact in ("好的，代码如下：\nprint(1)", "说明如下：（见下）\nprint(1)"):
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert result.ok, f"中文散文被过度收紧：{artifact!r} -> {result.reason()}"


@pytest.mark.unit
def test_cross_candidate_combination_is_not_pass() -> None:
    """跨候选拼凑不得通过：同一候选必须自己过完三个谓词。

    反例形状：裸标识符围栏（`parses` 过、可执行语句不过）+ 语法错误围栏
    （可执行语句谓词因 SyntaxError 返回 None，免检）。各取一个就"全过"。
    """
    artifacts = (
        "```\n好\n```\n```\ndef f(:\n```",
        "```\n1 + 1\n```\n```\ndef f(:\n```",
        "```\n好\n```\n```\nx = (\n```",
    )
    for artifact in artifacts:
        result = verify(artifact, PYTHON_FILE_BASELINE)
        assert not result.ok, f"跨候选拼凑被判通过：{artifact!r}"


@pytest.mark.unit
def test_failure_message_points_at_best_candidate() -> None:
    """全不过时报错指向「失败谓词最少」的候选，而不是围栏符号行。"""
    result = verify("```\n好\n```\n```\ndef f(:\n```", PYTHON_FILE_BASELINE)
    assert not result.ok
    reason = result.reason()
    assert reason, "被拒但没有原因"
    # ` ``` ` 那行不是 Python，落在它上面的语法错误说明选错了候选
    assert "第 1 行" not in reason or "语法错误" not in reason, f"报错指向了围栏行：{reason}"


@pytest.mark.unit
def test_extract_module_importable_from_verifier() -> None:
    """拆文件不得丢对外导出：旧路径 `aide.core.verifier.extract_candidates` 必须仍可用。"""
    assert extract_candidates("print(1)") == ["print(1)"]
    assert extract_code("```python\nprint(1)\n```").strip() == "print(1)"
