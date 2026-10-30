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

按声明的 skill 分组，共 **45 条**（A8 + B6 + C6 + D11 + E7 + F2 + G5）。
**每条断言都必须能在没有网络、没有真实模型的情况下跑通**（用假 provider）。

### A. 上下文与缓存（`context-engineering`、`kv-cache-design`）

| 断言 | 断言什么 |
|---|---|
| `test_prefix_hash_stable_across_turns` | 连续 10 轮，前 3 轮静态前缀 sha256 完全相同 |
| `test_prefix_contains_no_volatile_data` | 静态前缀里不出现时间戳、随机 ID、运行序号；few-shot 示例若存在必须固定，不得按请求检索 |
| `test_tool_definitions_not_in_messages` | 工具定义走请求顶层 `tools` 字段，**不得**以消息形式混入 `messages`（角色体系只允许 system/user/assistant/tool） |
| `test_tool_definitions_never_mutated_midrun` | 工具定义块 sha256 与**顺序**在单次运行内不变；按需发现只能发生在未进入前缀的通道 |
| `test_message_roles_respect_provider_constraint` | 连续同角色消息按 provider 约束合并，不产生非法角色序列；不自行拼接 `USER:/ASSISTANT:` 文本格式 |
| `test_context_order_is_prefix_trace_statebar` | 装配结果的段序固定，位置断言而非存在断言 |
| `test_session_restore_reproduces_prefix_bytes` | 会话恢复后重建的消息序列与中断前逐字节一致（含替换字符串冻结） |
| `test_reasoning_echo_matches_provider_contract` | 历史思考按厂商契约回传：DeepSeek V4 / Kimi K2 类在带 `tools` 时必须原样回传 `reasoning_content`，R1 类必须剥离 |

### B. 状态栏（`agent-state-bar`）

| 断言 | 断言什么 |
|---|---|
| `test_state_bar_appended_as_last_user_message` | 状态栏是最后一条 **user** 消息，用 `<agent_status>` 包裹；位置与角色双断言 |
| `test_state_bar_has_five_fields` | 五项齐全：时间戳、工具调用计数、TODO 快照（含 pending/in_progress/completed/cancelled）、最近错误摘要、系统状态（cwd / OS / Shell / Python 版本） |
| `test_tool_counter_maintained_by_code` | 计数由 runtime 递增。构造"模型自称调用了 3 次、实际 2 次"的轨迹，断言计数为 **2**（skill：模型会无条件相信状态栏，统计必须由代码做） |
| `test_state_bar_replaced_each_turn` | 第 N 轮只存在一条状态栏；历史状态栏被替换（若改用持久追加模式，必须同时给出 token 增长曲线的实测） |
| `test_state_bar_survives_compression` | 压缩后状态栏仍是最末一条，且内容为当前轮而非旧轮 |
| `test_state_bar_not_polluted_by_external_content` | 来自网页/工具结果的外部内容**不得**被直接写入状态栏（投毒面：模型高度信任状态栏） |

### C. 压缩（`context-compression`）

| 断言 | 断言什么 |
|---|---|
| `test_compression_trigger_uses_declared_threshold` | 阈值显式声明（默认 **窗口 80%**）；未达阈值不压缩、达到才压缩 |
| `test_compression_batch_with_compressed_marker` | 触发时**一次性批量**压所有未标记结果，不是每轮零碎压；已压内容打 `[COMPRESSED]` 标记且永不重复处理 |
| `test_compression_is_layered_light_to_heavy` | 按 skill 的五层由轻到重：工具结果预算控制 → 删除噪声 → API 层微压缩 → 归档式摘要 → 全量压缩。**子 Agent 隔离是替代方案，不是层**，不得混入层级序列 |
| `test_compression_keeps_trace_pointer` | 压缩后仍保留原始轨迹索引，可回溯到被压缩的那几步 |
| `test_status_bar_only_compression_is_refused` | **陷阱守卫**：把上下文压到"只剩系统提示 + 状态栏"的策略必须被拒绝或标记高危——状态栏是有损投影，只留它会在未覆盖维度上断崖下降 |
| `test_summary_preserves_four_categories` | 摘要必须显式保留 decisions / constraints / failures / citations 四类，缺任一类即失败 |

### D. 故障与恢复（`error-recovery`）

四层故障分类按 skill 口径：**API 层 / 工具层 / 上下文层 / 控制流层**。

| 断言 | 断言什么 |
|---|---|
| `test_fault_classified_into_four_layers` | 每个错误归入上述四层之一；**未知错误不得默认归入可重试**，必须显式标 `unknown` |
| `test_retry_only_for_retryable_classes` | 只有 API 层的限流/过载/超时/连接中断走重试；参数非法、权限不足、工具不存在**原样重试一律拒绝** |
| `test_retry_backoff_is_bounded` | 指数退避 + 随机抖动 + 尊重服务端等待时长；有上限与最大次数，超限进入熔断 |
| `test_circuit_breaker_opens_then_half_opens` | 连续失败达阈值（默认 **3**，来源：Claude Code 生产统计）后熔断打开；冷却后进入半开探测，成功一次才恢复 |
| `test_repeated_tool_fingerprint_detected` | 对「工具名 + 参数」算指纹，相同指纹反复出现即判无进展循环 |
| `test_stream_idle_watchdog_kills_stalled_stream` | 独立空闲看门狗：连接建立但无数据超过阈值即判定卡死并主动重试（SDK 超时只覆盖连接，不覆盖传输） |
| `test_stream_interruption_resumes_from_last_block` | 流式中断后从最后一个完整块续传；半截工具调用转文字补完并**按指纹去重**，不重复已执行的副作用 |
| `test_error_path_never_calls_llm` | 错误处理路径上禁用一切会再次调模型的副作用逻辑（死亡螺旋防护），并用递归深度计数器兜底 |
| `test_compression_circuit_breaker_after_3_failures` | 压缩连续失败 3 次即放弃压缩并暴露，不进入反复压缩失败的循环 |
| `test_fallback_preserves_prefix` | 切备用厂时请求前缀逐字节一致；厂商私有凭证一律丢弃，历史工具调用改写为文字叙述 |
| `test_fallback_and_circuit_events_recorded` | 接管、熔断、恢复动作写入轨迹（否则 M5 无法归因） |

### E. 执行边界与验收基线（`coding-agent-harness`）

skill 的四组件：**验收基线 / 执行边界 / 反馈信号 / 回退手段**，本节按此成组。

| 断言 | 断言什么 |
|---|---|
| `test_tool_runs_inside_sandbox_root` | 工具执行的工作目录被限制在 sandbox root；写 `../` 被拒 |
| `test_readonly_paths_enforced` | `book/` 与旧 RAG 仓库路径对写操作直接拒绝，不只是提示词约束 |
| `test_dangerous_action_requires_approval` | `write` / `exec` 类未审批时被执行层拒绝；破坏性捷径（删库重建、删光重写）走专门检查 |
| `test_every_task_declares_acceptance_baseline` | 每个任务应用注册时必须声明"验收基线命令"；缺失则注册失败 |
| `test_feedback_signal_is_structured` | 反馈信号必须结构化喂回：文件路径 + 行号 + 错误类型 + 建议，不得只给一句"失败了" |
| `test_rollback_available_before_destructive_action` | 破坏性动作前必须存在可用回退（快照/分支），无回退手段时拒绝执行 |
| `test_parallel_batch_failure_isolation` | 一个工具失败只在同批并行调用内传播，不取消独立调用、不上升为整个任务中止 |

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
| `test_verifier_rejects_obviously_bad_output` | verifier 签名固定为 `verify(artifact, baseline)`；测试**直接传入最小基线**，构造空文件 / 语法错误脚本 / 只改注释的补丁，断言返回 **False** 并给出可定位原因 |

> 这一组是「约束」格子的核心。没有前三条，防不住循环停不下来与 token 失控；
> 没有后两条，防不住"假成功"。
>
> **后两条必须成对**：`..._requires_verifier` 只证明「verifier 在环内」，不证明「verifier 判得对」。
> 一个永远返回 True 的空实现能让它全绿。`..._rejects_obviously_bad_output` 才证明 verifier 有**区分力**。
> `loop-engineering` 的原话：**验证不可靠时，循环转得越快，只是把劣质产出更快地标记为完成。**
>
> 且这条**必须在写 verifier 之前定稿**——否则会退化成"实现写成什么样，断言就迁就成什么样"。
>
> **接口约束**：verifier 必须接受**显式基线参数**（`verify(artifact, baseline)`），而不是自己去任务注册表里查。
> 这条决定了 G 组是否自足：若 verifier 只从任务注册读基线，G 就得等 E 做完才能测，
> 而 E 又排在 G 之后——规则"测不了就标 xfail、不得改软"会在第一处冲突上失效。

## 三之二、实现顺序与依赖（施工图）

断言可以平铺，实现不能平铺。只列**有前置依赖**的条目，未列出的视为无依赖。

**全局实现顺序**：
A（前缀与消息结构）→ B（状态栏）→ G（闸门与验证）→ C（压缩）→ D（故障恢复）→ E（执行边界）→ F（入口登记）

| 断言 | 依赖 | 为什么 |
|---|---|---|
| `test_budget_limit_terminates` | LLM 客户端的 token 用量计量 | 没有计量就无法在预算耗尽时准确停下 |
| `test_reasoning_echo_matches_provider_contract` | provider 能力表声明是否支持 reasoning 回传 | 无法判断"该回传还是该剥离" |
| `test_state_bar_survives_compression` | B 状态栏 + C 压缩 | 两样都在才能测交叉行为 |
| `test_compression_batch_with_compressed_marker` | C 的压缩入口统一收口 | 收口才能批量与打标 |
| `test_compression_is_layered_light_to_heavy` | C 至少三级由轻到重的策略 | 只有一种策略时"由轻到重"无意义 |
| `test_status_bar_only_compression_is_refused` | 同上（收口） | 收口了才能拦极端策略 |
| `test_compression_circuit_breaker_after_3_failures` | C 的压缩失败计数 | 需要失败计数才有熔断 |
| `test_circuit_breaker_opens_then_half_opens` | D 的故障分类能区分"连续失败" | 分类不到位无法判定何时开熔断 |
| `test_stream_idle_watchdog_kills_stalled_stream` | 块级流式解析 | 不按块解析就无法判定"无新数据" |
| `test_stream_interruption_resumes_from_last_block` | 同上 + 调用指纹表 | 续写前要能去重 |
| `test_error_path_never_calls_llm` | 错误路径与模型调用分层 | 分不清就会在错误路径里再调模型 |
| `test_fallback_and_circuit_events_recorded` | `runtime/trace` 可用 | 没有轨迹就没有写入目标 |
| `test_completion_claim_requires_verifier` | `core/verifier` 接口存在 | 先有接口才能断言"调用过" |
| `test_feedback_signal_is_structured` | E 的工具错误封装 | 结构化错误是反馈信号的原料 |
| `test_rollback_available_before_destructive_action` | 快照机制 | 没有快照就无从判断"是否可回退" |
| `test_every_task_declares_acceptance_baseline` | 任务注册机制 | 无注册点则无处校验 |

> `test_verifier_rejects_obviously_bad_output` **不在上表中**：它曾经依赖 E 组的"任务声明验收基线"，
> 而全局顺序是 G 在 E 之前——依赖成环，规则当场失效。解法是把基线从"任务注册表"下移到**函数参数**
> （`verify(artifact, baseline)`），测试自带最小基线，G 组因此自足并与 E 解耦。
> E 组只保留产品约束：任务上线必须声明基线。

**规则（防"顺手改软"）**：后实现的功能**不得**顺手放宽前面的断言。
某条暂时测不了时，标 `xfail` 并写明原因与解除条件，**不得删除或改软**。

## 四、人工判断

1. **随便挑一轮，你能不能说出"它为什么做这个动作"？** 说不出来说明上下文装配里有黑箱。
2. **`make replay` 的 `prefix_sha256` 只在压缩触发时变化吗？** 其他时间变化即违反前缀不变性。
3. **工具失败时回传给模型的是结构化错误，还是 Python 堆栈？** 前者可据以调整策略，后者不可。

## 五、Skill 留痕（必填，空着即未完成）

- 加载（2026-10-30 全部正式加载，非读文件代替）：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`

**采纳的结论（skill 结论 → 支撑的断言）**

1. **上下文 = 四种角色 + 顶层 `tools` 字段**（`context-engineering`）→ 新增 `test_tool_definitions_not_in_messages`；角色断言限定在 system/user/assistant/tool 四类。
2. **三条铁律：前缀不改 / 动态追加末尾 / 用标准格式**（`kv-cache-design`）→ 强化 `test_prefix_contains_no_volatile_data`（含 few-shot 必须固定）、`test_message_roles_respect_provider_constraint`（禁止自行拼接文本格式）。
3. **工具定义顺序必须固定，不得按使用频率排序**（`kv-cache-design` 反模式表）→ `test_tool_definitions_never_mutated_midrun` 从"内容不变"扩为"内容与顺序都不变"。
4. **会话恢复要能重建与缓存字节流一致的序列**（`kv-cache-design` 清单第 5 条）→ 新增 `test_session_restore_reproduces_prefix_bytes`。
5. **历史思考回传策略各家相反**（`kv-cache-design`：R1 剥离、V4/K2/GLM-5 必须回传否则 400）→ 新增 `test_reasoning_echo_matches_provider_contract`。
6. **状态栏用 user 角色 + `<agent_status>` 包裹 + 放最末**（`agent-state-bar`）→ `test_state_bar_appended_as_last_user_message` 增加角色与包裹双断言。
7. **统计必须由代码维护**（`agent-state-bar`："模型几乎无条件相信状态栏"）→ `test_tool_counter_maintained_by_code` 的反例构造法照抄该结论。
8. **状态栏是投毒面**（`agent-state-bar` 陷阱）→ 新增 `test_state_bar_not_polluted_by_external_content`。
9. **80% 阈值 + 批量压缩 + `[COMPRESSED]` 防重复**（`context-compression` 自适应窗口化）→ 阈值写死 80%，新增 `test_compression_batch_with_compressed_marker`。
10. **压缩必保四类：decisions / constraints / failures / citations**（`context-compression`）→ `test_summary_preserves_four_categories`（原 `..._open_loops` 范围过窄，已替换）。
11. **四层故障是 API / 工具 / 上下文 / 控制流**（`error-recovery`）→ 修正 D 组口径（原写"传输/协议/工具/语义"，属臆造）。
12. **可重试与不可重试必须分流**（`error-recovery`）→ 新增 `test_retry_only_for_retryable_classes`。
13. **指纹去重 + 每路径独立失败计数**（`error-recovery`）→ 新增 `test_repeated_tool_fingerprint_detected`。
14. **空闲看门狗**（`error-recovery`："SDK 超时只覆盖连接不覆盖传输"）→ 新增 `test_stream_idle_watchdog_kills_stalled_stream`。
15. **死亡螺旋防护**（`error-recovery`）→ 新增 `test_error_path_never_calls_llm`。
16. **熔断阈值 3 次来自生产统计**（`error-recovery`，Claude Code 案例）→ 阈值写死 3 并注明来源；新增 `test_compression_circuit_breaker_after_3_failures`。
17. **跨厂商接管：中立轨迹、凭证一律丢弃**（`error-recovery`）→ 并入 `test_fallback_preserves_prefix` 的描述。
18. **Harness 四组件**（`coding-agent-harness`）→ E 组按"验收基线 / 执行边界 / 反馈信号 / 回退手段"重排，补 3 条：反馈信号结构化、回退可用性、并行故障隔离。
19. **约束优先于指导**（`coding-agent-harness`）→ 支撑 E 组全部断言走"代码强制"而非"提示词建议"。
20. **压缩与状态栏是同一枚硬币两面**（`context-compression`）→ 决定 C 组只做减法、B 组只做加法，两者不互相替代。

**冲突与裁决**

1. **D 组四层命名错误（已改）**：原写"传输 / 协议 / 工具 / 语义"，skill 明确是"API / 工具 / 上下文 / 控制流"。按 skill 改，不保留臆造口径。
2. **C 组分层次序错误（已改）**：原写"截断 → 摘要 → 子 Agent 隔离"。skill 的隔离是"优于压缩的**替代方案**"，不是最重的一层；真正的五层是"预算控制 → 删噪声 → API 微压缩 → 归档摘要 → 全量压缩"。已按 skill 重写。
3. **状态栏实现方式（采纳 skill 的选择规则）**：M1 默认**实现一（每轮替换）**——状态栏含 TODO 与错误摘要，属"较大且更新频繁"，符合 skill 的"状态较大/更新频繁/轨迹长 → 选替换"。同时保留 `AIDE_STATE_BAR_MODE` 开关，以便实测两模式的成本曲线（skill 给出了 `C_替换` 与 `C_追加` 的估算式）。
4. **状态栏放置位置 vs 前缀稳定**：状态栏每轮替换会让"上次注入之后的后缀"失效。这不是违反铁律——铁律保护的是**静态前缀**，状态栏位于末尾，失效范围只有一轮。已在 B 组断言中体现，不作为冲突。

**实测差异**

- 加载 skill 前清单为 32 条，加载后为 45 条（净增 13 条，1 条替换）。净增不是凑数：每条都能指回上面 20 条结论中的具体一条，且多数是"原清单完全没有覆盖的面"（reasoning 回传、指纹去重、看门狗、回退手段、并行隔离）。
- **口径错误 2 处**（D 组四层、C 组分层次序）是 skill 直接纠正的，不是我的判断。这说明"凭印象写规格"会在最基础的名词上出错。

## 六、拒绝条件（任一命中即不通过）

- 状态栏被塞进系统提示词（破坏前缀稳定性 / KV Cache）。
- 工具定义混入 `messages` 或以文本格式自行拼接角色。
- 工具调用计数由模型自述而非代码维护。
- 模型自称完成即退出，未经过验证器。
- verifier 是永远返回 True 的空实现（缺区分力断言即视为未完成）。
- 轨迹缺少 token 或成本字段，导致 M5 无法算单位任务成本。
- 熔断后直接失败，而不是按轨迹前缀接管到备用厂商。
- 压缩后丢失原始轨迹指针，无法回溯。
- 出现"只剩状态栏"的极端压缩且未被拦。
- 不可重试错误被原样重试。
- 错误处理路径里再次调用 LLM。
- 危险工具仅靠提示词约束，没有执行层拦截。
- 破坏性动作前没有可用回退。
- 新增 `src/` 模块未登记进 `ENTRY_MODULES`。
