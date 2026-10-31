# M1 验收：Harness 内核

**落在哪个格子**：LLM + 上下文 + 约束（唯一跨格子的里程碑，因为它就是 Harness 本身）
**书中依据**：ch1「Agent 入门」Harness 工程；ch2「上下文工程」（全书最关键）；ch5 `provider-failover` 故障接管
**skill**：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`
**核心判断（ch1）**：能跑的 Demo 与可靠产品之间的差距在 Harness，不在模型。

> 本文是 M1 的**规格书**。断言先于代码：清单未定清就写实现，等于让实现去猜验收。
>
> **出处列是本规格的可信度标记**：能给出 `skill:行号` 的，说明结论有原文依据；
> 写「**自定**」的，说明是我在没有原文支撑时做的工程判断——这类条目必须在下一次审查时优先复核。

## 一、模块拆解

| 子模块 | 落在哪个格子 | 实现文件 | 书中依据 |
|---|---|---|---|
| provider 抽象 + 熔断接管 | LLM | `src/aide/llm/` | ch5 `provider-failover` |
| 上下文装配（静态前缀 + 轨迹 + 尾部状态栏） | 上下文 | `core/context.py`、`core/state_bar.py` | ch2 KV Cache 前缀不变性 |
| 上下文压缩 | 上下文 | `core/compressor.py` | ch2 `context-compression` |
| 主循环 + 三重闸门 + 终止判定 | 约束 | `core/loop.py`、`core/verifier.py` | ch10 过早终止；`loop-engineering` |
| 执行策略纯函数 | 约束 | `core/policy.py` | `coding-agent-harness` 执行边界 |
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

按声明的 skill 分组，**M1 可执行 47 条**（A8 + B6 + C8 + D11 + E5 + F3 + G6）。
另有 7 条因依赖 M2/M4 的系统能力而移到后续阶段，见「三之四、阶段边界」。

**每条断言都必须能在没有网络、没有真实模型的情况下跑通**（用假 provider / 纯函数）。

### A. 上下文与缓存（`context-engineering`、`kv-cache-design`）

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_prefix_hash_stable_across_turns` | 连续 10 轮，前 3 轮静态前缀 sha256 完全相同 | M1 | kv-cache:20-22 |
| `test_prefix_contains_no_volatile_data` | 前缀里不出现时间戳/随机 ID/运行序号；few-shot 示例若存在必须固定，不得按请求检索 | M1 | kv-cache:21；context-eng:41 |
| `test_tool_definitions_not_in_messages` | 工具定义走顶层 `tools` 字段，不得以消息形式混入 `messages` | M1 | context-eng:21 |
| `test_tool_definitions_never_mutated_midrun` | 工具定义内容与**顺序**在单次运行内不变 | M1 | kv-cache:20,64,79 |
| `test_message_roles_valid_and_not_text_spliced` | 角色只用 system/user/assistant/tool；不自行拼接 `USER:/ASSISTANT:` 文本。**M1 不做连续同角色合并**，理由见 ADR 0004（厂商契约需真实 API 才能验证，且合并会破坏状态栏位置不变量） | M1 | kv-cache:22；context-eng:21；ADR 0004 |
| `test_context_order_is_prefix_trace_statebar` | 段序固定，位置断言而非存在断言 | M1 | kv-cache:33；context-eng:22 |
| `test_assembly_is_deterministic` | 同进程内用同一轨迹重建装配结果，字节一致（完整跨进程恢复见 M4） | M1 | **自定**（见 ADR 0003） |
| `test_reasoning_echo_matches_provider_contract` | R1 类剥离历史思考；V4/K2/GLM-5 带 `tools` 时必须原样回传 `reasoning_content` | M1 | kv-cache:46,52-54 |

### B. 状态栏（`agent-state-bar`）

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_state_bar_appended_as_last_user_message` | 最后一条 **user** 消息，`<agent_status>` 包裹；位置与角色双断言 | M1 | state-bar:63-70 |
| `test_state_bar_has_five_fields` | 时间戳、工具计数、TODO（四态）、最近错误摘要、系统状态（cwd/OS/Shell/Python） | M1 | state-bar:82-88 |
| `test_tool_counter_maintained_by_code` | 构造"模型自称 3 次、实际 2 次"，断言计数为 **2** | M1 | state-bar:95 |
| `test_state_bar_replaced_each_turn` | 第 N 轮只有一条状态栏；若改持久追加，须给出 token 增长曲线实测 | M1 | state-bar:63-70 |
| `test_state_bar_survives_compression` | 经真实 `compress()` 压缩后，状态栏仍是最末一条、内容为当前轮、且压缩确实变小（原占位切片实现已替换） | M1 | state-bar:97 |
| `test_state_bar_not_polluted_by_external_content` | 网页/工具结果不得被直接写入状态栏 | M1 | state-bar:96 |

### C. 压缩（`context-compression`）

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_compression_trigger_uses_declared_threshold` | 阈值显式声明（默认**窗口 80%**）；未达不压、达到才压 | M1 | compress:49 |
| `test_compression_batch_with_compressed_marker` | 一次性批量压未标记结果；已压内容打 `[COMPRESSED]` 且不重复处理 | M1 | compress:61-69 |
| `test_compression_is_layered_light_to_heavy` | 按五层由轻到重：预算控制→删噪声→API 微压缩→归档摘要→全量压缩。**隔离是替代方案，不是一层**。M1 只实现 1/2/4/5 四层，第 3 层（API 微压缩）不可用须留 skipped 记录 | M1 | compress:26,61-69 |
| `test_compression_records_skipped_layers_with_reason` | 不可用的层（API 微压缩）必须出现在 `steps` 里并带跳过原因，**不得静默跳过**——否则"五层"变成"四层"而无人察觉 | M1 | compress:61-69 |
| `test_count_tokens_weights_cjk_per_char` | 中文按 1 字 1 token；改回 `len//3` 这类英文系数即红（否则压缩阈值在生产上永不触发） | M1 | **自定**（ADR 0005） |
| `test_compression_keeps_trace_pointer` | 压缩后保留原始轨迹索引，可回溯 | M1 | compress:61-69 |
| `test_status_bar_only_compression_is_refused` | 压到"只剩系统提示+状态栏"必须被拒或标记高危 | M1 | state-bar:97 |
| `test_summary_preserves_four_categories` | 摘要必保 decisions / constraints / failures / citations | M1 | compress:80 |

### D. 故障与恢复（`error-recovery`）

四层按 skill 口径：**API 层 / 工具层 / 上下文层 / 控制流层**。

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_fault_classified_into_four_layers` | 归入四层之一；未知错误不得默认可重试，须显式标 `unknown` | M1 | error:19-24,26 |
| `test_retry_only_for_retryable_classes` | 仅 API 层限流/过载/超时/中断走重试；参数非法、权限不足、工具不存在原样重试一律拒绝 | M1 | error:26,64 |
| `test_retry_backoff_is_bounded` | 指数退避+抖动+尊重服务端等待；有上限与最大次数，超限进熔断 | M1 | error:32,42 |
| `test_circuit_breaker_opens_then_half_opens` | 连续失败达阈值（默认 **3**）后打开；冷却后半开探测，成功一次才恢复 | M1 | error:42 |
| `test_repeated_tool_fingerprint_detected` | 对「工具名+参数」算指纹，重复即判无进展循环 | M1 | error:26 |
| `test_stream_idle_watchdog_kills_stalled_stream` | 独立空闲看门狗：连接建立但无数据超阈值即判定卡死并主动重试 | M1 | error:28,65 |
| `test_stream_interruption_resumes_from_last_block` | 从最后一个完整块续传；半截工具调用转文字补完并按指纹去重 | M1 | error:46,59,71 |
| `test_error_path_never_calls_llm` | 错误处理路径禁用会再调模型的副作用逻辑，并用递归深度计数兜底 | M1 | error:44,69 |
| `test_compression_circuit_breaker_after_3_failures` | 压缩连续失败 3 次即放弃并暴露 | M1 | error:42；compress:69 |
| `test_fallback_preserves_prefix` | 切厂时前缀逐字节一致；厂商私有凭证一律丢弃，历史调用改写为文字叙述 | M1 | error:61 |
| `test_fallback_and_circuit_events_recorded` | 接管、熔断、恢复动作写入轨迹 | M1 | **自定**（M5 归因需要） |

### E. 执行边界：policy 纯函数（`coding-agent-harness`）

设计：`core/policy.py` 是**纯函数** `evaluate(action) -> allow | deny | need_approval`，不执行任何真实操作。
M1 只测这个函数；M2 接工具时执行器调用同一个 policy —— 一次实现，两处受益，
且 M1 结束时策略逻辑已被老数据验证过。这是把"对系统的依赖"降为"对参数的依赖"。

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_policy_denies_path_escape` | `evaluate({"type":"write","path":"../etc/x"})` → **deny** | M1 | harness:45 |
| `test_policy_denies_write_to_readonly_root` | 写 `book/` 或旧 RAG 仓库 → **deny** | M1 | AGENTS.md 二节；harness:45 |
| `test_policy_requires_approval_for_exec` | `exec` 类动作 → **need_approval** | M1 | harness:45 |
| `test_policy_blocks_destructive_pattern` | `rm -rf` / 删库重建 / 覆盖未读文件 → deny 或 need_approval | M1 | harness:45 |
| `test_policy_denies_symlink_escape` | 解析 symlink 后逃出 sandbox root → deny | M1 | **自定，无 skill 出处** |

### F. 机制登记（入口 + 留痕）

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_skill_load_is_logged` | 清单声明的 skill 均在 `docs/skills-load.log` 内，且哈希与当前 `SKILL.md` 一致（skill 升级即红） | M1 | AGENTS.md:62 |
|---|---|---|---|
| `test_all_entry_modules_registered` | `src/` 下新增任何模块都必须出现在 `ENTRY_MODULES`，否则测试红 | M1 | **自定**（本仓机制） |
| `test_offline_applied_before_sdk_import` | 入口在干净子进程按自身顺序导入后：**若加载了第三方 SDK**（openai/httpx/transformers/torch/huggingface_hub），须满足 `HF_HUB_OFFLINE=1` 且 HF 常量为 True；纯标准库模块不判定 | M1 | **自定**（M0 教训） |

### G. 预算、终止与验证（`loop-engineering`）

| 断言 | 断言什么 | 可执行 | 出处 |
|---|---|---|---|
| `test_turn_limit_terminates_with_reason` | 轮次耗尽返回结构化终止原因并落轨迹，不得静默返回空答案 | M1 | **自定**（M0 回归补回） |
| `test_budget_limit_terminates` | token 预算耗尽停止，不抛异常，返回部分结果+原因 | M1 | **自定** |
| `test_timeout_limit_terminates` | 墙钟超时生效，已完成步骤轨迹不丢。**仅覆盖"多次慢调用累积超预算"**；单次调用永久挂起由 D 组 `test_stream_idle_watchdog_kills_stalled_stream` 负责，`max_seconds` 对此无效 | M1 | **自定** |
| `test_completion_claim_requires_verifier` | 模型自称完成不得直接终止；须经 verifier 判定 | M1 | loop-engineering:17,19 |
| `test_verifier_rejects_obviously_bad_output` | `verify(artifact, baseline)`；空文件/语法错误/只改注释 → False 且给出可定位原因 | M1 | loop-engineering:18 |
| `test_verify_all_without_baselines_is_not_pass` | 多基线合并时**空基线不得默认通过**（循环体不执行 → failures 为空 → 原本会 ok=True，与单基线走 `VERIFICATION_UNAVAILABLE` 的语义相反） | M1 | **自定**（"空实现能过"变体） |

> 后两条必须成对：前者只证明「verifier 在环内」，一个永远返回 True 的空实现同样能过；
> 后者才证明「verifier 有区分力」。且必须在写 verifier 之前定稿。
>
> **接口约束**：verifier 接受**显式基线参数**，不自己去任务注册表查。否则 G 要等 E 之后的 M4，
> 规则"测不了就标 xfail、不得改软"会在第一处冲突上失效。

## 三之二、实现顺序与依赖（施工图）

**全局实现顺序**：
A（前缀与消息结构）→ B（状态栏）→ G（闸门与验证）→ C（压缩）→ D（故障恢复）→ E（policy）→ F（入口登记）

| 断言 | 依赖 | 为什么 |
|---|---|---|
| `test_budget_limit_terminates` | LLM 客户端 token 计量 | 无计量无法在预算耗尽时准确停下 |
| `test_reasoning_echo_matches_provider_contract` | provider 能力表声明 | 无法判断该回传还是该剥离 |
| `test_state_bar_survives_compression` | B + C | 两样都在才能测交叉 |
| `test_compression_batch_with_compressed_marker` | C 压缩入口统一收口 | 收口才能批量与打标 |
| `test_compression_is_layered_light_to_heavy` | C 至少三级由轻到重 | 单策略时"由轻到重"无意义 |
| `test_status_bar_only_compression_is_refused` | 同上（收口） | 收口才能拦极端策略 |
| `test_compression_circuit_breaker_after_3_failures` | C 的压缩失败计数 | 有计数才有熔断 |
| `test_circuit_breaker_opens_then_half_opens` | D 能区分"连续失败" | 分类不到位无法判定开熔断 |
| `test_stream_idle_watchdog_kills_stalled_stream` | 块级流式解析 | 不按块解析无法判定"无新数据" |
| `test_stream_interruption_resumes_from_last_block` | 同上 + 调用指纹表 | 续写前要能去重 |
| `test_error_path_never_calls_llm` | 错误路径与模型调用分层 | 分不清就会在错误路径里再调模型 |
| `test_fallback_and_circuit_events_recorded` | `runtime/trace` 可用 | 没轨迹就没写入目标 |
| `test_completion_claim_requires_verifier` | `core/verifier` 接口 | 先有接口才能断言"调用过" |

**规则（防"顺手改软"）**：后实现的功能**不得**顺手放宽前面的断言。
测不了就标 `xfail` 并写明原因与解除条件，**不得删除或改软**。

## 三之三、C 组施工图（五层映射与依赖）

> 开工前先定这张表，因为它决定"断言名覆盖到哪里"。上一轮刚修过一类问题：
> **断言名比实现覆盖范围宽**。压缩最容易复发——"五层压缩"听起来都做了，
> 实际可能只做了触发点。

### 1. 五层在 M1 的落点

| 层 | 书里要求（compress:61-69） | M1 落地函数 | 实现？ | 理由 |
|---|---|---|---|---|
| 1 工具结果预算控制 | 大输出存磁盘，模型只看摘要预览；替换决策做出即冻结 | `cap_tool_output(text, max_chars) -> Capped` | ✅ 纯函数 | 落盘属 M2 工具层；M1 只做**截断决策 + 冻结语义** |
| 2 噪声直接删除 | 低价值内容直接移除，不摘要 | `drop_noise(messages, policy) -> (kept, dropped)` | ✅ 纯函数 | 判定必须显式（`disposable=True` 或超龄），不能让模型自由裁量 |
| 3 API 层微压缩 | 服务端移除前缀中指定工具结果 | **无** | ❌ **M1 不实现** | 依赖 provider 的 context-editing 能力，本地既不可实现也不可验证。**必须在计划里显式记录"跳过原因"**，不得静默跳过 |
| 4 归档式摘要 | 逐轮结构化摘要，像 git log 而非 squash | `archive_summarize(turns, summarize_fn)` | ✅ 注入 summarizer | summarizer 作参数传入（可为假函数），因此可测 |
| 5 全量压缩 | LLM 驱动 + 连续失败熔断 | `full_compress(messages, summarize_fn, breaker)` | ✅ 注入 summarizer + 熔断 | 熔断阈值 3，复用 G 组连续失败计数 |

### 2. 编排：`compress()`

```
compress(messages, limits, count_tokens, summarize_fn=None) -> CompressionResult
```

- 按层**由轻到重**尝试，直到估算 token 低于 `limits.compression_threshold()`
- 每层产出一条 `CompressionStep(layer, changed, reason)`；**跳过的层也必须留 step**（`changed=False, reason="..."`）
- 被压缩过的消息打 `[COMPRESSED]` 标记，**永不二次处理**
- 返回 `CompressionResult(messages, steps, refused, refusal_reason)`
- **拒绝分支**：若压缩后只剩"系统提示 + 状态栏"，返回 `refused=True`，不改动消息（`test_status_bar_only_compression_is_refused`）

### 3. token 估算口径（必须先定，否则阈值无意义）

M1 用**可注入**的 `count_tokens: Callable[[str], int]`，默认实现 **CJK 按 1 字 1 token、其余 4 字符 1 token**
（见 ADR 0005）。第一版曾用 `len(text)//3`，在中文语料上低估约 3 倍：

- 窗口 128K、阈值 80% 时，`len//3` 要等到 307K 字符才认为达标，压缩**在生产上永不触发**
- 而断言只依赖同一口径的相对关系，所以测试全绿、线上失效——最隐蔽的一类

- 断言只依赖"同一个 count_tokens 下的相对关系"，不硬编码绝对 token 数
- `test_count_tokens_weights_cjk_per_char` 挡住回退：改回英文系数即红
- M5 接真实 tokenizer 时只替换这一个函数，阈值语义不变

### 4. 断言 → 依赖 → 顺序

| 顺序 | 断言 | 依赖 | 说明 |
|---|---|---|---|
| 1 | `test_compression_trigger_uses_declared_threshold` | `count_tokens` + `compression_threshold` | 未达阈值不压、达到才压 |
| 2 | `test_compression_batch_with_compressed_marker` | 层 1（cap）+ 标记 | 一次性批量压未标记结果，已压不重复 |
| 3 | `test_compression_is_layered_light_to_heavy` | 至少三层可用（1/2/4） | 层 3 不实现，但必须出现在 skipped 记录里 |
| 4 | `test_compression_keeps_trace_pointer` | 层 4（archive） | 压缩后仍能回溯原始轨迹索引 |
| 5 | `test_summary_preserves_four_categories` | 层 4 + summarizer | decisions / constraints / failures / citations |
| 6 | `test_compression_records_skipped_layers_with_reason` | 编排器 skipped 列表 | **层 3 不可用时不得静默跳过** |
| 7 | `test_status_bar_only_compression_is_refused` | 编排器拒绝分支 + B 组状态栏 | 跨组断言 |
| — | `test_compression_circuit_breaker_after_3_failures`（D 组） | 层 5 + G 组连续失败计数 | 连续 3 次失败即放弃 |

### 5. 两条交叉断言的处置（必须写清，否则就是同名不同义）

| 断言 | 现状 | 处置 |
|---|---|---|
| `test_state_bar_survives_compression`（B 组） | **已改为真实调用**：C 组落地时同步替换，现调用 `compress()` 并断言状态栏仍为末条、内容为当前轮、`tokens_after <= tokens_before` | 原占位（`trajectory[-2:]` 切片）已删除，不存在"同名不同义" |
| `test_status_bar_only_compression_is_refused`（C 组） | 未实现 | 依赖编排器拒绝分支 + B 组 `build_state_bar` |

## 三之四、阶段边界（这 7 条不属于 M1）

| 断言 | 移到 | 为什么 |
|---|---|---|
| `test_tool_runs_inside_sandbox_root` | M2 | 需要真实工具执行；M1 由 `test_policy_denies_path_escape` 覆盖判定逻辑 |
| `test_dangerous_action_requires_approval` | M2 | 需要执行层；M1 由 `test_policy_requires_approval_for_exec` 覆盖策略 |
| `test_feedback_signal_is_structured` | M2 | 需要真实工具错误封装 |
| `test_rollback_available_before_destructive_action` | M2 | 需要快照机制 |
| `test_parallel_batch_failure_isolation` | M2 | 需要并行工具调用 |
| `test_session_restore_reproduces_prefix_bytes` | M4 | 需要真实会话持久化；M1 降级为 `test_assembly_is_deterministic` |
| `test_every_task_declares_acceptance_baseline` | M4 | 需要任务注册机制 |

## 四、人工判断

1. **随便挑一轮，你能不能说出"它为什么做这个动作"？** 说不出来说明上下文装配里有黑箱。
2. **`make replay` 的 `prefix_sha256` 只在压缩触发时变化吗？** 其他时间变化即违反前缀不变性。
3. **工具失败时回传给模型的是结构化错误，还是 Python 堆栈？** 前者可据以调整策略，后者不可。

## 五、Skill 留痕（必填，空着即未完成）

- 加载（2026-10-30 全部正式加载）：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`
- 采纳结论：见各表「出处」列，逐条标注 `skill:行号`。
  M1 可执行 43 条中：**35 条有行号、8 条标自定、0 条缺失**（`make check-provenance` 可复核）。
  此前写"33 条有行号 / 12 条自定"是错的——两者相加 45 与 43 不符，实际是 2 条只写了 skill 名没写行号，被用户按"既无行号也未标自定 = 不通过"查出。
- 冲突与裁决：

  1. **D 组四层命名错误（已改）**：原写"传输/协议/工具/语义"，skill 明确为 **API/工具/上下文/控制流**（error-recovery:19-24）。按 skill 改，不保留臆造口径。
  2. **C 组分层次序错误（已改）**：原把"子 Agent 隔离"当最重一层；skill 里隔离是**替代方案**（compress:26,82），真正的五层见 compress:61-69。
  3. **状态栏实现方式**：默认**实现一（每轮替换）**——含 TODO 与错误摘要属"较大且更新频繁"，符合 state-bar:69-70 的选择规则；保留 `AIDE_STATE_BAR_MODE` 开关以实测两模式成本（state-bar:72-77 给了估算式）。
  4. **M1 阶段边界**：把 E 组从"验证系统"降级为"验证纯函数"（policy），系统级断言移到 M2/M4。这与 `verify(artifact, baseline)` 是同一手法：**把对系统的依赖改成对参数的依赖**。

- 实测差异：
  1. 加载 skill 前 32 条，加载后 M1 可执行 43 条、外移 7 条。净增不是凑数：每条可指回具体行号。
  2. **2 处口径是 skill 直接纠正的**，不是我的判断——说明"凭印象写规格"会在最基础的名词上出错，且能通过所有下游检查（下游查的是"实现符合规格"，不查"规格符合现实"）。
  3. 出处列暴露了 8 条**自定**条目，其中 `test_policy_denies_symlink_escape` 完全没有原文支撑，属我加的工程判断，需下次审查优先复核。
  4. 另有 2 条只写了 skill 名未写行号（`test_completion_claim_requires_verifier`、`test_verifier_rejects_obviously_bad_output`），
     违反本规格自己的规则「既无行号也未标自定 = 不通过」。补行号后为 35 / 8 / 0。
     **审查建议本身也要过同一道检验**——这两条正是上一轮审查提出的建议。

## 六、拒绝条件（任一命中即不通过）

- 状态栏被塞进系统提示词（破坏前缀稳定性 / KV Cache）。
- 工具定义混入 `messages` 或以文本格式自行拼接角色。
- 工具调用计数由模型自述而非代码维护。
- 模型自称完成即退出，未经过验证器。
- verifier 是永远返回 True 的空实现。
- 轨迹缺少 token 或成本字段，导致 M5 无法算单位任务成本。
- 熔断后直接失败，而不是按轨迹前缀接管到备用厂商。
- 压缩后丢失原始轨迹指针，无法回溯。
- 出现"只剩状态栏"的极端压缩且未被拦。
- 不可重试错误被原样重试。
- 错误处理路径里再次调用 LLM。
- 危险动作仅靠提示词约束，`policy` 里没有对应 deny / need_approval 分支。
- 新增 `src/` 模块未登记进 `ENTRY_MODULES`。
- 断言表里有条目既无 `skill:行号` 也未标**自定**（等于隐藏了先验填空）。
