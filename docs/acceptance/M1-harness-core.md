# M1 验收：Harness 内核

**落在哪个格子**：LLM + 上下文 + 约束（唯一跨格子的里程碑，因为它就是 Harness 本身）
**书中依据**：ch1「Agent 入门」Harness 工程；ch2「上下文工程」（全书最关键）；ch5 `provider-failover` 故障接管
**skill**：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`
**核心判断（ch1）**：能跑的 Demo 与可靠产品之间的差距在 Harness，不在模型。

> 本文是 M1 的**规格书**。断言先于代码：清单未定清就写实现，等于让实现去猜验收。

## 一、模块拆解

| 子模块 | 落在哪个格子 | 实现文件 | 书中依据 |
|---|---|---|---|
| provider 抽象 + 熔断接管 | LLM | `src/aide/llm/` | ch5 `provider-failover` |
| 上下文装配（静态前缀 + 轨迹 + 尾部状态栏） | 上下文 | `core/context.py`、`core/state_bar.py` | ch2 KV Cache 前缀不变性 |
| 上下文压缩 | 上下文 | `core/compressor.py` | ch2 `context-compression` |
| 主循环 + 三重闸门 + 终止判定 | 约束 | `core/loop.py`、`core/verifier.py` | ch10 过早终止；`loop-engineering` |
| 执行边界 | 约束 | `core/policy.py` | `coding-agent-harness` 执行边界 |
| 轨迹落盘 | 可观测性 | `runtime/trace.py` | ch7 可观测性 |

## 二、命令

```bash
make run TASK="总结我今天要做的三件事"
# 预期：终端逐轮打印行动，最后落 traces/<id>.jsonl

make replay TRACE=traces/<id>.jsonl
# 预期：逐轮输出 轮次 / prefix_sha256 / 状态栏内容 / 工具调用 / 观察 / tokens / cost

make run TASK="..." AIDE_MAX_TURNS=1
# 预期：以「轮次耗尽」结构化终止原因退出，轨迹记录该原因，不得静默返回空答案

make test
```

## 三、自动检查（规格）

按声明的 skill 分组，共 **32 条**（A5 + B5 + C5 + D6 + E4 + F2 + G5）。
**每条断言都必须能在没有网络、没有真实模型的情况下跑通**（用假 provider）。

### A. 上下文与缓存（`context-engineering`、`kv-cache-design`）

| 断言 | 断言什么 |
|---|---|
| `test_prefix_hash_stable_across_turns` | 连续 10 轮，前 3 轮静态前缀 sha256 完全相同 |
| `test_prefix_contains_no_volatile_data` | 静态前缀里不出现时间戳、随机 ID、运行序号（否则每轮缓存失效） |
| `test_tool_definitions_never_mutated_midrun` | 单次运行内工具定义块 sha256 不变；按需发现只能在"未进入前缀"的通道里发生 |
| `test_message_roles_respect_provider_constraint` | 连续同角色消息按 provider 约束合并，不产生非法角色序列 |
| `test_context_order_is_prefix_trace_statebar` | 装配结果的段序固定，位置断言而非存在断言 |

### B. 状态栏（`agent-state-bar`）

| 断言 | 断言什么 |
|---|---|
| `test_state_bar_appended_as_last_user_message` | 状态栏是最后一条 user 消息，位置断言 |
| `test_state_bar_has_five_fields` | 五项齐全：时间戳、工具调用计数、TODO 快照、最近错误摘要、系统状态（cwd / OS / Python 版本） |
| `test_tool_counter_maintained_by_code` | 计数由 runtime 递增。构造"模型自称调用了 3 次、实际 2 次"的轨迹，断言计数为 **2** |
| `test_state_bar_replaced_each_turn` | 第 N 轮只存在一条状态栏；历史状态栏被替换（若改用持久追加模式，必须同时给出 token 增长曲线的实测） |
| `test_state_bar_survives_compression` | 压缩后状态栏仍是最末一条，且内容为当前轮而非旧轮 |

### C. 压缩（`context-compression`）

| 断言 | 断言什么 |
|---|---|
| `test_compression_trigger_uses_declared_threshold` | 阈值显式声明（占用比例或绝对 token）；未达阈值不压缩、达到才压缩 |
| `test_compression_is_layered_light_to_heavy` | 按"截断 → 摘要 → 子 Agent 隔离"由轻到重尝试，不得一上来就摘要 |
| `test_compression_keeps_trace_pointer` | 压缩后仍保留原始轨迹索引，可回溯到被压缩的那几步 |
| `test_status_bar_only_compression_is_refused` | **陷阱守卫**：把上下文压到"只剩系统提示 + 状态栏"的策略必须被拒绝或标记高危——这是断崖式质量下降的典型来源 |
| `test_summary_preserves_open_loops` | 摘要后仍保留未完成的 TODO 与关键决策，可从轨迹断言 |

### D. 故障与恢复（`error-recovery`）

| 断言 | 断言什么 |
|---|---|
| `test_fault_classified_into_four_layers` | 四层故障分类（传输 / 协议 / 工具 / 语义）落地；未知错误**不得默认归入可重试**，必须显式标 `unknown` |
| `test_retry_backoff_is_bounded` | 指数退避有上限与最大次数，超限即进入熔断，不存在无限重试 |
| `test_circuit_breaker_opens_then_half_opens` | 连续失败达阈值后熔断打开；冷却期后进入半开探测，成功一次才恢复 |
| `test_stream_interruption_resumes_from_last_block` | 流式中断后从最后一个完整块续传，不重复整段 |
| `test_fallback_preserves_prefix` | 切备用厂时请求前缀逐字节一致 |
| `test_fallback_and_circuit_events_recorded` | 接管与熔断事件写入轨迹（否则 M5 无法归因） |

### E. 执行边界与验收基线（`coding-agent-harness`）

| 断言 | 断言什么 |
|---|---|
| `test_tool_runs_inside_sandbox_root` | 工具执行的工作目录被限制在 sandbox root；写 `../` 被拒 |
| `test_readonly_paths_enforced` | `book/` 与旧 RAG 仓库路径对写操作直接拒绝，不只是提示词约束 |
| `test_dangerous_action_requires_approval` | `write` / `exec` 类未审批时被执行层拒绝 |
| `test_every_task_declares_acceptance_baseline` | 每个任务应用注册时必须声明"验收基线命令"；缺失则注册失败，不允许无验收标准的任务上线 |

### F. 入口登记（`AGENTS.md` 第七节）

| 断言 | 断言什么 |
|---|---|
| `test_all_entry_modules_registered` | `src/` 下新增任何模块文件都必须出现在 `ENTRY_MODULES` 里，否则测试红 |
| `test_offline_applied_before_sdk_import` | 每个入口模块在干净子进程里按自身顺序导入后，`huggingface_hub.constants.HF_HUB_OFFLINE` 为 True |

### G. 预算、终止与验证（`loop-engineering`）

| 断言 | 断言什么 |
|---|---|
| `test_turn_limit_terminates_with_reason` | 轮次耗尽返回**结构化终止原因**，轨迹记录该原因，不得静默返回空答案 |
| `test_budget_limit_terminates` | token 预算耗尽时停止，**不抛异常**，返回部分结果 + 预算耗尽原因 |
| `test_timeout_limit_terminates` | 墙钟超时生效，且**已完成步骤的轨迹不丢** |
| `test_completion_claim_requires_verifier` | 模型自称完成**不得**直接终止循环；必须经 `verifier` 判定，未通过则继续或带原因终止 |
| `test_verifier_rejects_obviously_bad_output` | 构造明显不满足验收基线的产物（空文件 / 语法错误的脚本 / 只改注释的补丁），断言 verifier 返回 **False** 并给出**可定位原因**（哪个基线项不满足） |

> 这一组是「约束」格子的核心。没有前三条，防不住循环停不下来与 token 失控；
> 没有后两条，防不住"假成功"。
>
> **后两条必须成对**：`..._requires_verifier` 只证明「verifier 在环内」，不证明「verifier 判得对」。
> 一个永远返回 True 的空实现能让它全绿。`..._rejects_obviously_bad_output` 才证明 verifier 有**区分力**。
> `loop-engineering` 的原话：**验证不可靠时，循环转得越快，只是把劣质产出更快地标记为完成。**
>
> 且这条**必须在写 verifier 之前定稿**——否则会退化成"实现写成什么样，断言就迁就成什么样"。

## 三之二、实现顺序与依赖（施工图）

断言可以平铺，实现不能平铺。只列**有前置依赖**的条目，未列出的视为无依赖。

**全局实现顺序**：
A（前缀与消息结构）→ B（状态栏）→ G（闸门与验证）→ C（压缩）→ D（故障恢复）→ E（执行边界）→ F（入口登记）

| 断言 | 依赖 | 为什么 |
|---|---|---|
| `test_budget_limit_terminates` | LLM 客户端的 token 用量计量 | 没有计量就无法在预算耗尽时准确停下 |
| `test_state_bar_survives_compression` | B 状态栏 + C 压缩 | 两样都在才能测交叉行为，单独实现任一项都测不了 |
| `test_compression_is_layered_light_to_heavy` | C 至少三种策略（截断 / 摘要 / 子 Agent 隔离） | 只有一种策略时"由轻到重"无意义 |
| `test_status_bar_only_compression_is_refused` | C 的压缩入口统一收口 | 收口了才能拦"只剩状态栏"的极端策略 |
| `test_circuit_breaker_opens_then_half_opens` | D 的故障分类能区分"连续失败" | 分类不到位就无法判定何时开熔断 |
| `test_fallback_and_circuit_events_recorded` | `runtime/trace` 可用 | 没有轨迹就没有写入目标 |
| `test_stream_interruption_resumes_from_last_block` | D 的块级流式解析 | 不按块解析就无法定位"最后一个完整块" |
| `test_completion_claim_requires_verifier` | `core/verifier` 接口存在 | 先有接口才能断言"调用过" |
| `test_verifier_rejects_obviously_bad_output` | E 的「每个任务声明验收基线」 | 没有基线就构造不出"明显不满足基线"的产物 |
| `test_every_task_declares_acceptance_baseline` | 任务注册机制 | 无注册点则无处校验 |

**规则（防"顺手改软"）**：后实现的功能**不得**顺手放宽前面的断言。
某条暂时测不了时，标 `xfail` 并写明原因与解除条件，**不得删除或改软**——
这条正是 M1 这次丢 3 条断言的直接教训。

## 四、人工判断

1. **随便挑一轮，你能不能说出"它为什么做这个动作"？** 说不出来说明上下文装配里有黑箱。
2. **`make replay` 的 `prefix_sha256` 只在压缩触发时变化吗？** 其他时间变化即违反前缀不变性。
3. **工具失败时回传给模型的是结构化错误，还是 Python 堆栈？** 前者可据以调整策略，后者不可。

## 五、Skill 留痕（必填，空着即未完成）

- 加载：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`
- 采纳结论：（逐条：前缀布局 / 缓存破坏模式 / 状态栏五项技术 / 压缩分层与阈值 / 四层故障分类与熔断上限 / Harness 四组件与执行边界）
- 冲突与裁决：
- 实测差异：

## 六、拒绝条件（任一命中即不通过）

- 状态栏被塞进系统提示词（破坏前缀稳定性 / KV Cache）。
- 工具调用计数由模型自述而非代码维护。
- 模型自称完成即退出，未经过验证器。
- 轨迹缺少 token 或成本字段，导致 M5 无法算单位任务成本。
- 熔断后直接失败，而不是按轨迹前缀接管到备用厂商。
- 压缩后丢失原始轨迹指针，无法回溯。
- 出现"只剩状态栏"的极端压缩且未被拦。
- 危险工具仅靠提示词约束，没有执行层拦截。
- 新增 `src/` 模块未登记进 `ENTRY_MODULES`。
