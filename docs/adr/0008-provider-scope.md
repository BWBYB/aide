# ADR 0008：M1 拆两段验收，Provider 接入划死范围

- 状态：已接受 ｜ 日期：2026-11-01 ｜ 关联：M1a / M1b

## 背景

M1 比 M0 大一个量级。若不划边界，"接 provider"会变成"最后十块"：
流式、多厂商、reasoning 回传、成本换算、错误码映射，每一样都能单独做一周。

## 决策一：拆段验收

| 段 | 内容 | 判据 |
|---|---|---|
| **M1a** Harness 内核 | 55 条断言 + 装配 + `make run` / `make replay` | 可独立验收 |
| **M1b** Provider 接入 | `llm/client.py` + 2 条断言 + contract 档 | 之后单独验收 |

M1a 的入口先用替身跑通，**先证明四件与模型无关的事**：
CLI 参数被读到、轨迹字段齐全、`AIDE_MAX_TURNS` 走出轮次耗尽、回放能读回轨迹。

顺序反过来（先接 provider 再让 `make run` 能跑）会导致：清单里三条命令在写下那天起
从未被执行过——等于三条没有验收的验收项。

## 决策二：M1b 的范围

| 做 | 不做 |
|---|---|
| 非流式单轮调用 | 流式响应（M1.5；策略①已定但无流可续） |
| 单厂商（DeepSeek 或 Kimi 二选一） | 多厂商 failover（`neutralize_request` 已是预备件） |
| 429/5xx 可重试、4xx 不可 | `reasoning_content` 回传（需 contract 档，M5 再说） |
| usage 解析进轨迹 | context-editing 微压缩（ADR 0006 已记） |
| 超时 + 重试接 D 组分类 | 成本估算精度（用 usage 原值，不做换算） |

预计 150–200 行 + 2 条断言：`test_provider_parses_usage_into_trace`、
`test_provider_classifies_4xx_as_non_retryable`。

不划范围的后果很具体：光"把真实错误码映射到四层分类"就能写出 20 条断言。

## 后果

- 正面：M1a 有明确完成边界，不会因 provider 的无底洞把整个 M1 拖成"永远差一点"。
- 负面：M1a 通过时，系统仍不能调用真实模型——这一点必须在 `status.md` 里写明，
  不能因为"55 条全绿"就暗示可用。
- 解除条件：M1b 完成，本节表格逐条勾选。
