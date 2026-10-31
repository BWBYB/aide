"""D 组（前两条）：故障分类 + 空闲看门狗。

看门狗用「纯判定 + 注入时钟」，不引入线程：
`should_kill()` 毫秒级可测，`abort` 回调证明真的会打断，两者都不依赖 sleep 竞态。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aide.core.recovery import FaultLayer, StreamWatchdog, classify, should_kill

NOW = datetime(2026, 10, 31, 10, 0, 0)


@pytest.mark.unit
def test_fault_classified_into_four_layers() -> None:
    """四层分类：API/工具/上下文/控制流；未知不得默认可重试。"""
    api = classify("RateLimitError")
    assert api.layer is FaultLayer.API and api.retryable is True

    tool = classify("InvalidToolArgs")
    assert tool.layer is FaultLayer.TOOL and tool.retryable is False

    context = classify("ContextOverflow")
    assert context.layer is FaultLayer.CONTEXT and context.retryable is False

    control = classify("DeadLoopDetected")
    assert control.layer is FaultLayer.CONTROL and control.retryable is False

    mystery = classify("SomethingWeird")
    assert mystery.layer is FaultLayer.UNKNOWN
    assert mystery.retryable is False, "未知错误被默认当成了可重试"


@pytest.mark.unit
def test_stream_idle_watchdog_kills_stalled_stream() -> None:
    """连接建立但不再吐数据时，看门狗必须主动中止。

    这条覆盖的是「永久挂起」，与 G 组的 `max_seconds` 互补：
    后者只在两次调用之间检查，依赖「调用会返回」。
    """
    aborted: list[str] = []
    watchdog = StreamWatchdog(idle_timeout_seconds=30, abort=aborted.append)

    assert should_kill(NOW - timedelta(seconds=29), NOW, 30) is False
    assert watchdog.check(NOW - timedelta(seconds=29), NOW) is False
    assert aborted == [], "未超时不应中止"

    assert should_kill(NOW - timedelta(seconds=31), NOW, 30) is True
    assert watchdog.check(NOW - timedelta(seconds=31), NOW) is True
    assert len(aborted) == 1 and "空闲超时" in aborted[0]

    # 重复检查不得重复中止（避免一次故障触发多次重试）
    watchdog.check(NOW - timedelta(seconds=41), NOW)
    assert len(aborted) == 1


@pytest.mark.unit
def test_should_kill_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError):
        should_kill(NOW, NOW, 0)
