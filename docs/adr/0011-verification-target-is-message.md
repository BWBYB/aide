# ADR 0011：验证对象是「模型回复」而不是「产物」

- 状态：已接受（**本轮不修**） ｜ 日期：2026-11-01 ｜ 关联：M1b / M2

## 现状

`src/aide/core/loop.py:209`：

```python
verification = verify(response.content or "", verifier_baseline)
```

判的是**模型回复的文本**，不是产物。

## 后果

1. 模型只在对话里贴一段代码、从不调用 `write_file`，也能通过验收——**没有东西被写出来**，但基线说"通过"。
2. 写对了文件，但回复措辞不同（例如只说"已完成，见 src/x.py"），可能不过。
3. 因此 `has_executable_statement` 目前实际在测"回复里有没有代码"，而不是"产物里有没有语句"。收紧判据（本轮已做）只是让"回一句『好的』"过不去，**没有改变验证对象错位这件事**。

## 决定

**M1b 不修**，理由：M1 没有真实工具执行器，`artifact` 只能来自对话文本；
在没有产物的情况下把验证对象改成"产物"，等于验证一个不存在的对象。

**M2 接真实执行器时必检**：`artifact` 必须换成产物内容或执行结果
（`tool_result` / 工作区文件 / 命令退出码），并且：

- 没有任何工具调用却声称完成 → 直接 `VERIFICATION_UNAVAILABLE`，不进入 `verify()`；
- 有产物时，`verify()` 的入参必须指向产物，不得回落到回复文本。

## 与既有断言的关系

本轮新增的三条基线断言（`test_baseline_rejects_bare_identifier` /
`test_baseline_rejects_bare_expression` / `test_baseline_accepts_call_and_statements`）
**不解决**本 ADR 的问题：它们让基线更严，但验证对象仍然是回复。

这条 ADR 的价值不是"以后再说"，而是把缺口变成**可检索**的文档：
M2 开工时按标题就能搜到，而不是靠某个人记得。
