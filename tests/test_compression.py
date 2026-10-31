"""C 组：压缩（8 条断言）。

`compress()` 是纯函数，summarizer 与 token 计数器都注入，因此全部可离线跑。
"""

from __future__ import annotations

import pytest

from aide.core.compressor import (
    COMPRESSED_MARK,
    Summary,
    archive_summarize,
    compress,
    count_tokens,
    only_system_and_state_bar,
)
from aide.core.loop import Limits
from aide.core.message import Msg, ToolCall

SYSTEM = "你是个人助理，回答要简短。"
STATE_BAR = "<agent_status>\ntime: 2026-10-31T09:00:00\ntool_calls: read_file=2\n</agent_status>"


def limits(window: int = 100) -> Limits:
    """窗口 100 token，阈值 80 —— 用小阈值把压缩逼出来。"""
    return Limits(max_turns=8, max_tokens=10**6, max_seconds=600, context_window=window)


def long_tool_result(size: int, marker: str = "内容") -> str:
    return marker * size


def base_messages(tool_size: int = 10) -> list[Msg]:
    return [
        Msg(role="system", content=SYSTEM),
        Msg(role="user", content="读一下 A 文件"),
        Msg(
            role="assistant",
            content=None,
            tool_calls=(ToolCall("c1", "read_file", {"path": "a.md"}),),
        ),
        Msg(role="tool", content=long_tool_result(tool_size), tool_call_id="c1"),
        Msg(role="user", content=STATE_BAR),
    ]


def fixed_summary(_chunk) -> Summary:
    return Summary(
        decisions=("选择 E3 作为默认链路",),
        constraints=("前缀必须字节稳定",),
        failures=("BM25 在短查询上失效",),
        citations=("book/chapter2.md#3",),
    )


@pytest.mark.unit
def test_count_tokens_weights_cjk_per_char() -> None:
    """中文按 1 字 1 token，不能沿用英文的 4:1 系数。

    `len(text)//3` 会把中文低估约 3 倍，后果不是"测试不稳"，而是生产阈值
    量级错误：128K 窗口 80% 触发点要等到 307K 字符才认为达标。
    """
    chinese = "上下文压缩" * 50  # 250 个汉字
    assert count_tokens(chinese) == 250
    assert count_tokens(chinese) > len(chinese) // 3 * 2, "中文被低估了 3 倍"

    english = "the quick brown fox jumps over the lazy dog"  # 43 字符
    assert count_tokens(english) == len(english) // 4

    mixed = "前缀" + "abcdefgh"  # 2 CJK + 8 ASCII
    assert count_tokens(mixed) == 2 + 8 // 4


@pytest.mark.unit
def test_compression_trigger_uses_declared_threshold() -> None:
    """未达阈值不压，达到才压。"""
    small = [Msg(role="system", content=SYSTEM), Msg(role="user", content="短问题")]
    result = compress(small, limits=limits(10_000))
    assert not result.changed
    assert result.tokens_after == result.tokens_before
    assert result.steps[0].layer == "trigger"

    big = base_messages(tool_size=3000)
    triggered = compress(big, limits=limits(100), summarize_fn=fixed_summary)
    assert triggered.changed
    assert triggered.tokens_after < triggered.tokens_before

    # 压缩做了反而变大时必须回退（对噪声做摘要只是浪费 token）
    unhelpful = compress(base_messages(tool_size=400), limits=limits(100), summarize_fn=fixed_summary)
    assert not unhelpful.changed
    assert unhelpful.tokens_after == unhelpful.tokens_before
    assert any("已回退" in step.reason for step in unhelpful.steps)


@pytest.mark.unit
def test_compression_batch_with_compressed_marker() -> None:
    """超长工具结果一次性截断并打标，已打标的不重复处理。"""
    messages = base_messages(tool_size=3000)
    first = compress(messages, limits=limits(100), summarize_fn=fixed_summary)
    marked = [m for m in first.messages if COMPRESSED_MARK in (m.content or "")]
    assert marked, "没有任何消息被打上压缩标记"

    # 再压一次：已打标的不得被二次截断（幂等）
    second = compress(list(first.messages), limits=limits(100), summarize_fn=fixed_summary)
    for message in second.messages:
        if COMPRESSED_MARK in (message.content or ""):
            assert (message.content or "").count(COMPRESSED_MARK) == 1


@pytest.mark.unit
def test_compression_is_layered_light_to_heavy() -> None:
    """由轻到重：cap → drop_noise → (API 跳过) → archive → full。

    轻的一层够用时，重的层不应被执行。
    """
    heavy = compress(base_messages(tool_size=3000), limits=limits(100), summarize_fn=fixed_summary)
    names = heavy.layer_names()
    assert names.index("cap_tool_output") < names.index("api_micro_compression")
    assert names.index("api_micro_compression") < names.index("archive_summarize")
    assert names.index("archive_summarize") < names.index("full_compress")

    light = compress(base_messages(tool_size=3000), limits=limits(5_000), summarize_fn=fixed_summary)
    assert "archive_summarize" not in light.layer_names(), "轻层够用时不应进入重层"


@pytest.mark.unit
def test_compression_keeps_trace_pointer() -> None:
    """归档摘要必须带原始消息区间指针，可回溯被压缩的步骤。"""
    body = [
        Msg(role="user", content="第一步"),
        Msg(role="assistant", content="做完了第一步"),
        Msg(role="user", content="第二步"),
        Msg(role="assistant", content="做完了第二步"),
    ]
    archived, count = archive_summarize(body, fixed_summary, keep_recent=2)
    assert count == 2
    pointer = archived[0].content or ""
    assert "源消息 #0-#1" in pointer, f"缺少轨迹指针：{pointer}"


@pytest.mark.unit
def test_summary_preserves_four_categories() -> None:
    """摘要必须显式保留四类；空的也要渲染成"无"，不能省略。"""
    summary = Summary(decisions=("D1",), failures=("F1",))
    rendered = summary.render()
    for label in ("决策", "约束", "失败", "引用"):
        assert f"{label}:" in rendered
    assert "D1" in rendered and "F1" in rendered
    assert "约束: 无" in rendered, "空类别被省略了，模型无法判断'没有'还是'没说'"


@pytest.mark.unit
def test_compression_records_skipped_layers_with_reason() -> None:
    """第 3 层不可用时必须出现在 steps 里并带原因，不得静默跳过。"""
    result = compress(base_messages(tool_size=3000), limits=limits(100), summarize_fn=fixed_summary)
    skipped = [step for step in result.steps if step.layer == "api_micro_compression"]
    assert len(skipped) == 1, "不可用的层没有留痕"
    assert skipped[0].changed is False
    assert "context-editing" in skipped[0].reason


@pytest.mark.unit
def test_status_bar_only_compression_is_refused() -> None:
    """压到"只剩系统提示 + 状态栏"必须拒绝，且不改动原消息。"""
    messages = base_messages(tool_size=3000)

    def drop_everything(_chunk) -> Summary | None:
        return None  # summarizer 认为没有值得保留的内容

    result = compress(
        messages,
        limits=limits(100),
        summarize_fn=drop_everything,
        keep_recent=0,
    )
    assert result.refused, "只剩状态栏时未拒绝"
    assert result.refusal_reason and "状态栏" in result.refusal_reason
    assert result.messages == tuple(messages), "拒绝时不得改动消息"

    # 断言判据本身：只剩系统提示与状态栏时返回 True
    assert only_system_and_state_bar(
        [Msg(role="system", content=SYSTEM), Msg(role="user", content=STATE_BAR)]
    )
    assert not only_system_and_state_bar(
        [Msg(role="system", content=SYSTEM), Msg(role="user", content="还有真实对话")]
    )
