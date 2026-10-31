# ADR 0007：M1 的流式中断只做「丢弃重发」

- 状态：已接受 ｜ 日期：2026-10-31 ｜ 关联：M1 / D 组

## 背景

`error-recovery:46` 给了三种恢复方式：

| 策略 | 做法 | 代价 |
|---|---|---|
| ① 丢弃重发 | 半截内容整轮丢弃，重新发起 | 最贵但最稳 |
| ② 半截作 assistant 消息 | 要求模型接着写 | 依赖厂商原生支持 |
| ③ 元指令接续 | 追加一条元指令说明断点 | 拼接处易出重复字符 |

## 问题：断言名与原文不符

原清单写的是 `test_stream_interruption_resumes_from_last_block`
——「从最后一个完整块续传」。但原文**没有**「最后一个完整块」这个概念，
而且默认最稳的是①（丢弃重发）：它不是「续」，是「重来」。

如果实现的是①却把断言叫 `resumes`，就是"名字承诺 A、实际测 B"，
与此前 `test_message_roles_respect_provider_constraint`、
`test_state_bar_survives_compression` 是同一个毛病。

## 决策

1. **M1 只实现策略①**，断言改名为
   `test_stream_interruption_discards_partial_and_retries`，如实表达"丢弃 + 重发"。
2. **②③ 显式抛 `NotImplementedError`**，不静默降级——它们依赖厂商能力，
   本地无法验证（与压缩第 3 层的判断同构）。
3. **拼接归一化仍实现为纯函数** `normalize_splice()` + `validate_tool_arguments()`，
   因为这段逻辑与是否接真实流无关，且它是"**格式合法 ≠ 语义正确**"的唯一检查点：
   前若干条断言都在验结构对不对，这条验拼出来的东西还对不对。

## 解除条件

接入真实 provider 后：实现②或③，并按所选策略补各自的断言名
（`..._continues_with_partial_as_assistant` / `..._continues_with_meta_instruction`）。
若届时改用②③，`normalize_splice()` 才有真实调用路径；在此之前它是带测试的预备件。
