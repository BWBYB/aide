# ADR 0006：M1 的压缩只有四层

- 状态：已接受 ｜ 日期：2026-10-31 ｜ 关联：M1 / C 组

## 决策

`context-compression:61-69` 给了五层分层压缩，M1 实现其中四层：

| 层 | M1 落地 | 实现 |
|---|---|---|
| 1 工具结果预算控制 | `cap_tool_output()` | ✅ |
| 2 噪声直接删除 | `drop_noise()` | ✅ |
| 3 API 层微压缩 | 无 | ❌ |
| 4 归档式摘要 | `archive_summarize()` | ✅ |
| 5 全量压缩 | `full_compress()` | ✅ |

## 第 3 层为什么不做

它要求通过服务端的 context-editing 能力移除前缀里的指定工具结果，
本地既不可实现，也不可验证——`FakeProvider` 只能验「我们发了什么」，
验不了「服务端怎么删」。

## 缺口如何保持可见

1. `compress()` 的 `steps` 里**始终**包含 `api_micro_compression`，`changed=False` 并写明原因；
2. `test_compression_records_skipped_layers_with_reason` 断言这条记录存在且含 `context-editing`；
3. 本文档存在本身就是第七道保险——清单会随阶段更新被覆盖，ADR 不会。

## 风险与解除条件

- 风险：看清单以为「五层都做了」。**这是能力缺口看起来不像缺口的典型形态**，
  和「格式重排看起来不像删」同源。
- 解除条件：M2 接入真实 provider 后补第 3 层，届时必须重新验证
  「移除点之后缓存失效一次」的成本假设（compress:67），并补一条对照实验。
