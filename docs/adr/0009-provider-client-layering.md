# ADR 0009：Provider 客户端只发一次请求，不重试

- 状态：已接受 ｜ 日期：2026-11-01 ｜ 关联：M1b

## 背景

写 provider 客户端时，`try/except + 重试 + 退避 + 熔断` 几乎是本能反应。
但这些东西 **D 组已经全部实现**：`recovery.classify()` / `decide_retry()` /
`backoff_delay()` / `CircuitBreaker` / `FailureBreaker`。

如果 client 里再写一遍，就会出现两套重试策略、两个阈值、两份日志——
而且它们的判定必然逐渐分叉。

## 决策：三层职责，互不越界

| 层 | 职责 | 不做 |
|---|---|---|
| `llm/client.py` | 发一次请求；解析响应与 usage；把 HTTP/SDK 错误翻译成**带 `retryable` 标记的结构化异常** | 不重试、不退避、不熔断、不计数 |
| `recovery.py` | 决定重不重试、退避多久、何时熔断；维护连续失败计数 | 不认识 HTTP，只消费结构化异常 |
| `loop.py` | 编排：调 client、处理观察、判终止 | 不关心厂商差异 |

**判定标准**：`llm/client.py` 里出现 `for attempt in range(...)`、`while retries`、
`sleep(` 或 `except ... : continue` 形式的循环，就是重复实现。

## 如何被机器检查

新增守卫 `test_client_has_no_retry_loop`（M1b 生效）：用 AST 扫 `src/aide/llm/*.py`，
出现上述模式即红。定向规则与 `test_error_path_never_calls_llm` 同一手法。

`src/aide/llm/` 尚不存在时该测试 skip 并写明"待 M1b"——
**不允许**在没有 client 的情况下让它默默通过。

## 错误翻译约定

client 抛出的异常必须携带：

- `retryable: bool` —— 由 HTTP 语义决定（429/5xx/超时可重试；4xx 不可）
- `layer: api | tool | context | control` —— 供 `recovery.classify()` 直接使用
- `status_code: int | None`

这样 `recovery` 不需要判断 HTTP 码，`client` 不需要知道重试策略。

## 后果

- 正面：重试策略只有一个实现；阈值只有一处（3 次）；错误分类可单独测试。
- 负面：client 变"笨"，调用方必须自己套 `execute_with_retry`，多一层调用。
- 解除条件：若将来需要厂商特有的重试语义（如某家支持服务端 retry token），
  走 provider 能力表扩展 `recovery`，**不回到 client 里写循环**。
