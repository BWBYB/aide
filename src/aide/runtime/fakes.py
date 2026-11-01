"""可控的模型替身。

为什么这是 M1 的第一个文件：
`test_prefix_hash_stable_across_turns` 要「连续 10 轮」。没有可控的模型返回，
这条要么连真实 API（要 key、要网络、输出不确定、hash 可能抖），
要么写成"跑 10 轮看看一样不一样"——那测的是运气，不是设计。

43 条断言里约 20 条需要控制模型输出（A 组前缀、B 组状态栏、C 组压缩、G 组终止与验证）。
书稿那句「循环的瓶颈在验证器」有个推论：**测试基础设施是验证器的前提。**

三件事对应 D 组断言：
- `script`      → 按轮次返回，喂正常流程
- `fail_after`  → 注入 429/超时/流中断，喂熔断与看门狗
- `recorded`    → 完整请求体，喂前缀逐字节一致的断言
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence


class ManualClock:
    """可注入的时钟。

    为什么需要它：`test_timeout_limit_terminates` 要测"墙钟超时"，而超时与抛错是
    两条路径——`fail_after` 注入的是**立刻返回的异常**，不是"卡住不返回"。
    真睡（sleep）会让测试变慢且不稳定，所以让替身"在调用期间推进时钟"：
    这在语义上等价于"这次调用花了 N 秒"，而且是确定性的。
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class FakeProviderError(Exception):
    """替身抛出的错误基类。M1 的错误分类层建立后，换成真实分类。"""


class FakeRateLimitError(FakeProviderError):
    """模拟 API 层限流（可重试）。"""


class FakeTimeoutError(FakeProviderError):
    """模拟 API 层超时（可重试）。"""


class FakeStreamInterruptedError(FakeProviderError):
    """模拟流式中断（需要接续，不是简单重试）。"""


class FakeBadRequestError(FakeProviderError):
    """模拟不可重试错误（参数非法）。重试一万次结果相同。"""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    """一次模型返回。只保留框架真正需要消费的字段。"""

    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class FakeProvider:
    """按脚本返回的模型替身。

    - 脚本用尽后重复最后一条（模拟模型"卡在同一步"，供死循环/指纹断言使用）
    - `fail_after(n)` 在第 n 次调用后注入错误，供熔断、退避、看门狗断言使用
    - `recorded` 保存每次请求的完整负载，供前缀字节断言使用
    """

    def __init__(
        self,
        script: Sequence[ModelResponse],
        *,
        clock: "ManualClock | None" = None,
        latency_seconds: float = 0.0,
    ) -> None:
        if not script:
            raise ValueError("FakeProvider 需要至少一条脚本响应")
        self._script = list(script)
        self._fail_at: int | None = None
        self._fail_once: int | None = None
        self._error_factory: Any = FakeRateLimitError
        self._recorded: list[dict[str, Any]] = []
        self._calls = 0
        self._clock = clock
        self._latency = latency_seconds

    def fail_after(self, n: int, error_factory: Any = FakeRateLimitError) -> "FakeProvider":
        """第 n 次调用之后开始失败（n=0 表示第一次就失败）。"""
        if n < 0:
            raise ValueError("fail_after 的 n 不能为负")
        self._fail_at = n
        self._error_factory = error_factory
        return self

    def fail_once(self, error_factory: Any = FakeStreamInterruptedError) -> "FakeProvider":
        """只让下一次调用失败一次，之后恢复正常（测重试成功路径）。"""
        self._fail_once = self._calls
        self._error_factory = error_factory
        return self

    @property
    def recorded(self) -> list[dict[str, Any]]:
        return list(self._recorded)

    @property
    def calls(self) -> int:
        return self._calls

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        """接受完整请求体（messages + tools），返回一次响应或抛错。

        每次调用顺带推进注入的时钟 `latency_seconds`——这就是"这次调用花了 N 秒"，
        用来测墙钟超时，不需要真的 sleep。
        """
        self._recorded.append(request)
        index = self._calls
        self._calls += 1

        if self._clock is not None and self._latency:
            self._clock.advance(self._latency)

        if self._fail_once is not None and index == self._fail_once:
            self._fail_once = None
            raise self._error_factory(f"injected once at call #{index}")

        if self._fail_at is not None and index >= self._fail_at:
            raise self._error_factory(f"injected failure at call #{index}")

        return self._script[min(index, len(self._script) - 1)]


def scripted(*contents: str) -> list[ModelResponse]:
    """便捷构造：一串纯文本回复。"""
    return [ModelResponse(content=text) for text in contents]


def repeating(content: str, times: int) -> Iterable[ModelResponse]:
    """便捷构造：同一回复重复多次。"""
    for _ in range(times):
        yield ModelResponse(content=content)
