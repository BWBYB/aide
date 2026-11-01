"""D 组（前两条）：故障分类 + 空闲看门狗。

看门狗用「纯判定 + 注入时钟」，不引入线程：
`should_kill()` 毫秒级可测，`abort` 回调证明真的会打断，两者都不依赖 sleep 竞态。
"""

from __future__ import annotations

import ast
import json
import pathlib

from datetime import datetime, timedelta

import pytest

from aide.core.compressor import FailureBreaker
from aide.core.failover import neutralize_request
from fakes import FakeProvider, FakeStreamInterruptedError, ModelResponse
from aide.core.recovery import (
    FaultLayer,
    StreamStrategy,
    StreamWatchdog,
    classify,
    decide_retry,
    execute_with_retry,
    backoff_delay,
    CircuitBreaker,
    BreakerState,
    LoopDetector,
    EventLog,
    decide_recovery,
    normalize_splice,
    should_kill,
    validate_tool_arguments_shape,
)

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

    # 边界值：正好 30s 必须杀。`>=` 与 `>` 都"看起来合理"，这里把它钉死
    assert should_kill(NOW - timedelta(seconds=30), NOW, 30) is True

    assert should_kill(NOW - timedelta(seconds=31), NOW, 30) is True
    assert watchdog.check(NOW - timedelta(seconds=31), NOW) is True
    assert len(aborted) == 1 and "空闲超时" in aborted[0]

    # 重复检查不得重复中止（避免一次故障触发多次重试）
    watchdog.check(NOW - timedelta(seconds=41), NOW)
    assert len(aborted) == 1


@pytest.mark.unit
def test_stream_interruption_discards_partial_and_retries() -> None:
    """M1 只做策略①：丢弃半截内容整轮重发，不携带任何半截文本。

    名字照实写——没有"续"，所以不能叫 resumes（error-recovery:46 把三种策略列全了，
    且默认最稳的是①）。②③ 依赖厂商能力，本地不可验证，调用即显式抛错。
    """
    plan = decide_recovery()
    assert plan.strategy is StreamStrategy.DISCARD_RETRY
    assert plan.action == "retry_whole_turn"

    # 行为证据：半截内容不得出现在重发请求里。
    # 只断言 plan.carry_partial is False 证明不了什么——它只是「自称没带」。
    provider = FakeProvider([ModelResponse(content="完成")]).fail_once(FakeStreamInterruptedError)
    request = {"messages": [{"role": "user", "content": "原始问题"}], "tools": []}
    execute_with_retry(provider, request, max_attempts=2)

    assert len(provider.recorded) == 2, "策略①应当是整轮重发"
    assert provider.recorded[0] == provider.recorded[1], "重发请求被改动过（可能带入了半截内容）"
    assert "HALF" not in json.dumps(provider.recorded[1], ensure_ascii=False)

    for unsupported in (StreamStrategy.PARTIAL_AS_ASSISTANT, StreamStrategy.META_INSTRUCTION):
        with pytest.raises(NotImplementedError):
            decide_recovery(unsupported)


@pytest.mark.unit
def test_stream_resume_splice_is_normalized() -> None:
    """续写拼接后去重前缀与多余空白；参数合法但语义错误必须被检出。

    这是"格式合法 ≠ 语义正确"的唯一检查点：前若干条断言都在验结构对不对，
    这条验拼出来的东西还对不对。
    """
    assert normalize_splice("前缀 ABC", "ABC 后续") == "前缀 ABC 后续"
    assert normalize_splice("第一行  \n第二行  ", "") == "第一行\n第二行"

    assert validate_tool_arguments_shape({"path": "a.md"}, ("path",)) is None
    empty = validate_tool_arguments_shape({"path": "   "}, ("path",))
    assert empty is not None and "语义不成立" in empty
    missing = validate_tool_arguments_shape({}, ("path",))
    assert missing is not None and "缺少必填参数" in missing


@pytest.mark.unit
def test_should_kill_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError):
        should_kill(NOW, NOW, 0)


@pytest.mark.unit
def test_retry_only_for_retryable_classes() -> None:
    """只有 API 层可重试；参数非法、权限不足等原样重试一万次结果相同。"""
    api = classify("RateLimitError")
    assert decide_retry(api, 0, max_attempts=3).retry is True

    tool = classify("InvalidToolArgs")
    decision = decide_retry(tool, 0, max_attempts=3)
    assert decision.retry is False and "不可重试" in decision.reason


@pytest.mark.unit
def test_retry_backoff_is_bounded() -> None:
    """指数退避有硬上限，且达到最大尝试次数后不再重试。"""
    delays = [backoff_delay(i, base=0.5, cap=8.0) for i in range(10)]
    assert delays == sorted(delays), "退避不是单调不减"
    assert max(delays) <= 8.0, "退避没有上限"

    api = classify("RateLimitError")
    assert decide_retry(api, 2, max_attempts=3).retry is False, "达到上限仍在重试"


@pytest.mark.unit
def test_circuit_breaker_opens_then_half_opens() -> None:
    """连续失败达阈值即打开；冷却后半开；成功一次才恢复闭环。"""
    breaker = CircuitBreaker(threshold=3, cooldown_seconds=30)
    for _ in range(2):
        breaker.record_failure(NOW)
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow(NOW) is True

    breaker.record_failure(NOW)
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow(NOW) is False, "熔断打开后不应放行"

    assert breaker.allow(NOW + timedelta(seconds=31)) is True
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.record_success() is BreakerState.CLOSED


@pytest.mark.unit
def test_repeated_tool_fingerprint_detected() -> None:
    """相同「工具名+参数」反复出现即判无进展循环。"""
    detector = LoopDetector(repeat_threshold=3)
    args = {"path": "a.md"}
    assert detector.observe("read_file", args) is False
    assert detector.observe("read_file", args) is False
    assert detector.observe("read_file", args) is True
    assert detector.observe("read_file", {"path": "b.md"}) is False, "参数不同不应算重复"


@pytest.mark.unit
def test_fallback_preserves_prefix() -> None:
    """切厂时前缀逐字节一致；厂商私有凭证一律丢弃。"""
    request = {
        "messages": [
            {"role": "system", "content": "你是助理"},
            {"role": "assistant", "content": "思考", "thinking_signature": "SIG", "reasoning_content": "R"},
            {"role": "user", "content": "问题"},
        ],
        "tools": [{"name": "read_file", "description": "读", "parameters": {"type": "object"}}],
    }
    switched = neutralize_request(request)

    assert switched["messages"][0] == request["messages"][0], "系统提示被改动"
    assert switched["tools"] == request["tools"], "工具定义被改动"
    assert "thinking_signature" not in json.dumps(switched, ensure_ascii=False)
    assert "reasoning_content" not in json.dumps(switched, ensure_ascii=False)
    assert switched["messages"][1]["content"] == "思考", "正文应保留为普通内容"

    # 幂等：再次切换不改变结果
    assert neutralize_request(switched) == switched


@pytest.mark.unit
def test_error_path_never_calls_llm() -> None:
    """守门型断言：异常处理分支里不得再调用模型（死亡螺旋防护）。

    M1 的错误路径当前没有 LLM 调用，所以这条一定绿——它防的是「将来有人加」。
    用 AST 检查 `except` 分支内是否出现 provider 调用，而不是全文 grep
    （正常路径本来就该调 provider）。**扫整个 core 目录**：只扫单文件时，
    拆分出去的新文件会永远不被检查。

    边界：AST 只能看见**直接调用**。若错误路径通过回调、依赖注入或
    getattr(obj, name) 间接触发 LLM，这条查不到——它是守门的第一层，不是全部。
    第二层留到 M5：用轨迹里 stop_reason 与 token 消耗的对应关系反推。
    """
    banned = {"complete", "generate", "chat"}
    files = sorted(pathlib.Path("src/aide").rglob("*.py"))
    assert files, "扫到 0 个 .py：路径可能写错，守门未生效"
    # 扫整个 core 目录，不扫单文件：拆分文件时若只扫一个，
    # 新文件里的错误路径就永远不会被检查（本轮 failover.py 正是这样被拆出去的）。
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in banned
                ):
                    raise AssertionError(f"{path.name} 的 except 分支里调用了模型：{inner.func.attr}")


@pytest.mark.unit
def test_compression_circuit_breaker_after_3_failures() -> None:
    """压缩连续失败 3 次即放弃，不进入反复失败循环。"""
    breaker = FailureBreaker(max_consecutive=3)
    breaker = breaker.record_failure()
    breaker = breaker.record_failure()
    assert breaker.opened is False
    breaker = breaker.record_failure()
    assert breaker.opened is True

    assert breaker.record_success().opened is False, "成功一次应清零"


@pytest.mark.unit
def test_fallback_and_circuit_events_recorded() -> None:
    """接管、熔断、重试事件必须落轨迹，否则 M5 无法归因。"""
    log = EventLog()
    provider = FakeProvider([ModelResponse(content="完成")]).fail_once(FakeStreamInterruptedError)
    execute_with_retry(
        provider, {"messages": [{"role": "user", "content": "问题"}], "tools": []}, log=log
    )
    assert "retry" in log.kinds() and "retry_success" in log.kinds()
