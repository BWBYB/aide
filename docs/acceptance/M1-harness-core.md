# M1 验收：Harness 内核

**落在哪个格子**：LLM + 上下文 + 约束（唯一跨格子的里程碑，因为它就是 Harness 本身）
**书中依据**：ch1「Agent 入门」Harness 工程；ch2「上下文工程」（全书最关键）；ch5 `provider-failover` 故障接管
**skill**：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`
**核心判断（ch1）**：能跑的 Demo 与可靠产品之间的差距在 Harness，不在模型。

## 一、模块拆解

| 子模块 | 落在哪个格子 | 实现文件 | 书中依据 |
|---|---|---|---|
| provider 抽象 + 熔断接管 | LLM | `src/aide/llm/` | ch5 `provider-failover` |
| 上下文装配（静态前缀 + 轨迹 + 尾部状态栏） | 上下文 | `core/context.py`、`core/state_bar.py` | ch2 KV Cache 前缀不变性 |
| 上下文压缩 | 上下文 | `core/compressor.py` | ch2 `context-compression` |
| 主循环 + 三重闸门 + 终止判定 | 约束 | `core/loop.py`、`core/verifier.py` | ch10 过早终止；`loop-engineering` |
| 轨迹落盘 | 可观测性 | `runtime/trace.py` | ch7 可观测性 |

## 二、命令

```bash
make run TASK="总结我今天要做的三件事" 
# 预期：终端打印逐轮行动，最后落 traces/<id>.jsonl

make replay TRACE=traces/<id>.jsonl
# 预期：逐轮输出轮次 / prefix_sha256 / 状态栏内容 / 工具调用 / 观察 / tokens / cost
```

失败预期：

```bash
make run TASK="..." AIDE_MAX_TURNS=1
# 预期：以「轮次耗尽」结构化终止原因退出，且轨迹里记录该原因，不得静默返回空答案
```

## 三、自动检查（必须绿）

- `test_prefix_hash_stable_across_turns` —— 连续 10 轮，前 3 轮静态前缀 sha256 完全一致
- `test_state_bar_appended_not_prepended` —— 状态栏是最后一条 user 消息，位置断言
- `test_turn_limit_terminates_with_reason` —— 轮次耗尽返回结构化原因
- `test_budget_limit_terminates` —— token 预算耗尽时停止且不抛异常
- `test_timeout_limit_terminates` —— 墙钟超时生效
- `test_compression_keeps_trace_pointer` —— 压缩后仍保留原始轨迹索引，可回溯
- `test_fallback_preserves_prefix` —— 主厂商失败切备用厂商时，请求前缀逐字节一致
- `test_offline_env_set_before_sdk_import` —— 入口导入后 `os.environ["HF_HUB_OFFLINE"] == "1"`，
  且断言发生在 `import openai` / `import httpx` 之前（防静默联网，见 `AGENTS.md` 第七节）

## 四、人工判断

1. **随便挑一轮，你能不能说出"它为什么做这个动作"？** 说不出来说明上下文装配里有黑箱，不通过。
2. **`make replay` 输出的 prefix_sha256 在第 4 轮之后是否变化？** 应该只在压缩触发时才变，且变化点要有日志。
3. **错误信息是否可据以调整策略？** 故意让工具失败，看回传给模型的是结构化错误还是 Python 堆栈。

## Skill 留痕（必填，空着即未完成）

- 加载：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`
- 采纳结论：（逐条：前缀布局 / 状态栏技术 / 压缩阈值 / 熔断上限 / Harness 四组件）
- 冲突与裁决：
- 实测差异：

## 五、拒绝条件

- 状态栏被塞进系统提示词（破坏前缀稳定性 / KV Cache）。
- 模型自称完成即退出，未经过验证器。
- 轨迹缺少 token 或成本字段，导致 M5 无法算单位任务成本。
- 熔断后直接失败，而不是按轨迹前缀接管到备用厂商。
- 压缩后丢失原始轨迹指针，无法回溯。
