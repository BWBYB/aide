# 里程碑状态

**规则**：`待验收` → `完成` 的改动权在用户手里。AI 只能推进到 `待验收`。

| 里程碑 | 模块 | 状态 | 开始 | 验收人 |
|---|---|---|---|---|
| M0 知识层扩容 | 上下文 | **已完成** | 2026-10-30 | 用户 2026-10-30 勾选 |
| M1 Harness 内核 | LLM + 上下文 + 约束 | 待开始 | — | 待用户 |
| M2 工具层 | 工具 | 待开始 | — | 待用户 |
| M3 记忆系统 | 上下文 | 待开始 | — | 待用户 |
| M4 任务应用与事件 | LLM 策略 + 时序 | 待开始 | — | 待用户 |
| M5 任务层评估 | 验证 | 待开始 | — | 待用户 |
| M6 进化闭环 | 纠正 | 待开始 | — | 待用户 |
| M7 控制台 | 可观测性 | 待开始 | — | 待用户 |
| M8 交付 | 元层 | 待开始 | — | 待用户 |

## 未验证清单

暂无。

## 记录

### 2026-10-30：仓库初始化
- 更新位置：`pyproject.toml`、`Makefile`、`.env.example`、`AGENTS.md`、`README.md`、`docs/`
- 更新内容：初始化仓库骨架；把九个模块映射与十份验收清单落成文档（`docs/acceptance/`）。
- 与预期的偏差：知识层采取"路径桥接"而非迁移（用户指定旧 RAG 暂时不动），因此 `pyproject.toml` 不把旧包声明为依赖。
- 下一项：M0 语料扩容。

### 2026-10-30：M0 语料扩容完成，进入待验收
- 更新位置：`knowledge/`、`tests/test_corpus.py`、`docs/eval/retrieval-v2.md`
- 更新内容：语料从 5 文件 / 181 chunks 扩到 **24 文件 / 567 chunks**（书稿 12 文件 547 chunks + 个人资料 12 文件 20 chunks）；Dense 1024 维构建耗时 192.2s；在 v2 索引上重跑 E0–E4（21 题冻结测试集）。
- 与预期的偏差（**第一轮数字作废，见下条**）：首轮评估出现"全线下滑 9–19 个百分点"，当时按扩容代价解读。**该结论已撤回**——真实原因是索引缺陷，不是扩容。
- **发现的严重缺陷（M0 的真正产出）**：首轮 v2 索引的 Dense 向量与文本系统性错位。自相似度检查显示 `book/afterword.md#0` 仅 0.274、`personal/knowledge/profile.md#0` 仅 **0.001**（应为 1.000），"吴禹的求职方向"检索时正确 chunk 连 Top-8 都进不去。根因是旧包 `_last_token_pool` 用 `attention_mask.sum()-1` 定位末 token，而 tokenizer 明确 `padding_side="left"`，导致短文本取到 padding 位置。v1 索引因绝大多数 chunk 满 512 token 而侥幸未暴露。修复与论证见 `docs/adr/0002-embedding-pooling-fix.md`，并新增 `test_embeddings_self_consistent`（设 `AIDE_SLOW_TESTS=1` 启用）守住。
- 修复后重跑（**合并 36 题**，两套 gold 互补，覆盖全书 + 个人资料；candidate-k=20）：E0 Hit@5 = **0.9444**，E2/E3/E4 = 0.9167，E1 = 0.7222；MRR@10 上 E3 = **0.8123** 最高（E2 0.7098、E0 0.7685）；Recall@20 上 E2/E3 = **0.9861** 最高（E0 0.9444）。E3 p50 2265ms。
- **显著性与口径**：E0 与 E2 的 Hit@5 差 1 题（0.0278），21 题上 E3 的配对翻转为 improved 2 / degraded 1（discordant=3，**McNemar p = 1.0**）。正确表述是「**未观察到超出噪声的变化**」，不得写成"融合/重排提升了召回"或"伤害了召回"。15 题上 E2 高于 E3 的 1 题差同样**不能**说成"不是噪声"——差 1 题恰恰是最无法排除噪声的情形，正确表述是"方向可疑，待更大样本确认"。
- **36 题给出的可下结论部分**：三者的分工是清楚的——E0 在 Hit@5 上最高（0.9444），E2/E3 在 **Recall@20** 上最高（0.9861，+4.2 点），E3 在 **MRR@10/nDCG@10** 上最高（0.8123/0.7968，比 E2 高 10.2/9.3 点）。即：**融合的价值是扩大候选覆盖，重排的价值是排序深度，两者都不体现在 Top-5 命中率上**。延迟代价 61.9ms → 2265ms（36 倍）。
- **混杂必须说明**：v1 基线是在带缺陷的旧索引上测的，v2 已修复，所以两列差值 = 扩容效应 + 修 bug 效应，**不能归因到单一变量**。已在 `docs/eval/retrieval-v2.md` 顶部标注。
- **验收口径调整**：M0 不再以"对比 v1 漂移"为准（洁净 v1 基线需按修好的代码重建 181 chunks，约 20 分钟，只为让一个历史差值可相减，不划算），改为「**v2 在 36 题上的绝对水平 + 新增覆盖 + 人工抽查**」；"扩容净效应"明确标为**未测**，交由 M5 任务层回答。
- 首次失败与修正：`eval_v2.py` 首版把索引路径以相对路径传给子进程，而旧包在自身目录下执行，导致 `chunks.jsonl` 找不到；已改为 `Path.resolve()` 后重跑成功。
- **守卫从"可选项"改为"环内阻断"**：原自相似度校验只在 `pytest` 里且默认跳过（`AIDE_SLOW_TESTS`），等于守卫本身也是静默失效。现已在 `ingest_v2.py` 构建末尾强制执行 `verify_embeddings()`（模型在内存、batch_size=1、零额外网络），任一样本 < 0.999 直接 `exit 3` 并拒绝产出索引；`--verify-only` 可单独复验。**实测证明它能拦住历史缺陷**：对旧 v1 索引抽样 9 条，6 条失败，最差 0.0508。
- **ADR 编号理顺**：`0002` 归给 pooling 修复，新建 `docs/adr/0003-context-layout.md` 承接原先指向的上下文布局决策，`AGENTS.md` 的引用改为 0003。断链消除。
- **pooling 假设写成契约**：`_left_padded_last_token_pool` 现在断言 `attention_mask[:, -1]` 全为 1，上游若改回 right padding 会立即抛错，不再静默产错向量。
- **数字口径澄清**：新增题集是 **24 个 gold 引用 / 23 个去重 chunk**（`book/chapter7.md#8` 被 v2q07 与 v2q08 共用）。此前只写 23 未说明去重口径。
- 补充（skill 核对）：正式加载 `rag-pipeline` 复核 M0，结论已写入 `docs/acceptance/M0-corpus-v2.md` 的 Skill 留痕段。三处差异：① 固定窗口切分 vs skill 推荐的递归/结构感知切分——暂不改，为保持与 v1 同参数的单变量可比性；② 重排候选池 20 vs skill 建议的 50——暂不改，同为可比性；③ `recall@k` 学术口径与书中命中率口径不同——已在报告中加澄清段。另确认 E1 BM25 的 MRR 上升与 skill「精确术语靠稀疏」的判断一致。
- **candidate-k 20→50 单变量实验（证伪）**：Hit@5 0.9167→0.8889（−1 题），MRR@10 0.8123→0.8100，nDCG@10 0.7968→0.7976，p50 **2265ms→5602ms（×2.5）**。收益为零、延迟翻倍。**"候选池太小导致 E2>E3"的猜测被推翻**——我上一轮把最大落差（E0→E2 的融合段）误判成了重排段。结论：保持 candidate-k=20，不做该改动。
- **修正的表述**：此前说"E2 高于 E3，差 1 题，不是噪声能解释的"——这是逻辑倒置。15 题上 1 题 = 0.067，McNemar b=1/c=0 → p=1.0，差 1 题恰恰最无法排除噪声。已改为「方向可疑，待 36 题合并确认」；36 题合并后 E2 与 E3 的 Hit@5 完全相同（均 0.9167），差异只体现在 MRR/nDCG 上。
- **守卫的失败路径已拆分（用户发现）**：此前 `--verify-only` 在默认网络环境下会去连 HuggingFace，502 即崩，而"崩掉"和"向量错位"走同一条失败路径，退出码无法区分。改法：① `loader.py` 导入期默认设 `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`（模型已缓存时本就不该联网，需要下载时设 `AIDE_ALLOW_HF_NETWORK=1`）；② 退出码分层——`2` 语料/参数、`3` 数据错位、`4` 模型或索引不可用。三档已实测：正常索引 0、旧 v1 索引 3、缺失索引 4。
- **注释措辞修正**：`verify_embeddings` 原写"不依赖 pooling 修复本身"不准确——`load_embedding()` 会先装修复。准确说法是"`batch_size=1` 使结果与两种 pooling 实现都无关，因此它测的是**索引**，不是修复是否生效"。
- **两处数字修正**：① "E0 差 1–2 题"应为**正好 1 题**（34/36 vs 33/36）；② **E4 在 36 题上是负增益**——相对 E3 仅 MRR +0.0012、nDCG +0.0016，而 Recall@20 从 0.9861 降到 0.9722，代价是多一次 LLM 调用与 +187ms。按论证融合时同一套标准，E4 标为**保留待观察**，不作默认链路。
- **ADR 0002 的自相矛盾已修**：原文写"只有文件尾块与短文档才会触发，v1 占比很低，评估数字看起来正常"，与 `verify-index` 实测（`chapter2#10` 1899 字符、`#38` 862、`#17` 719 等**正文中段块**失败）矛盾。已按精确触发条件重写：**一个块只要在它所在 batch 里不是最长就会触发**；Markdown 按节聚合时短块在正文中段大量存在，不限于文件尾。同时挡掉"6/9 = 67%"的读法——抽样专挑最短块，属有偏抽样，只能说**存在系统性错位，比例未测**。结论改为：v1 的 0.857 不可作为对照基准。
- **exit 2 语义改准**：argparse 自身也返回 2，因此 2 定义为「**调用方错误**」（参数非法 / 上游路径缺失 / 语料清单不符），不再写"语料问题"；需判定环境问题看 4。M0 清单已加"修法"列。
- **离线设置抽成 `knowledge/_offline.py`**：原先的 import 时序假设（`ingest_v2` 先 import loader）在 M1 会失效——`llm/provider.py` 必然直接 import SDK，若它先于 `knowledge.loader` 被导入，`HF_HUB_OFFLINE` 就设晚了，且**不报错只变慢**。现单点实现 + `AGENTS.md` 第七节硬约束 + M1 断言 `test_offline_env_set_before_sdk_import`。
- **元观察（可迁移的那句）**：同一个失效模式已经出现**三次**，本质都是**把"未发生"当成"通过"**——
  ① 自相似度守卫默认被 `skipif` 跳过：守卫存在，但从不执行；
  ② 守卫执行了，但"网络崩"与"数据坏"共用退出码：执行了，但结果不可判读；
  ③ 离线设置依赖 import 时序：设置存在，但可能设晚，且失败表现为"变慢"而非报错。
  共同点：**校验/配置的失败路径没有被单独设计**，因此它总是以"看起来正常"的形态存在。可迁移的规则是——**凡是新增一道检查，必须同时回答"它失败时，别人怎么看出来"**。
- **M0 已由用户勾选为「完成」（2026-10-30）**。用户意见：离线功能本身成立（四档退出码实测、无网络请求），断言盲区只影响"将来顺序被改坏时能否发现"，不影响当前功能。
- **按勾选条件先修断言，再开 M1**：原断言的盲区已实测证实——先 `import transformers` 再 `import loader` 时，`constants.HF_HUB_OFFLINE=False` 而 `os.environ.HF_HUB_OFFLINE=1`，旧断言会通过。已换成 `tests/test_offline_order.py`：**每个入口模块在干净子进程里按自己的顺序导入，然后检查 `huggingface_hub.constants.HF_HUB_OFFLINE` 常量**（不是环境变量）。坏顺序探针实测 `exit=10`。
- **残留边界写进 M0 清单的「已知遗留」**：新守卫按 `ENTRY_MODULES` 清单探测，目前只含 `ingest_v2`/`eval_v2`；**M1 新增 `aide.llm.client`、`aide.tools.mcp_client` 时必须登记**，否则覆盖不到真正会先 `import openai` 的模块。这条已列为 M1 开工前硬前置。
- 下一项：M1 Harness 内核。**顺序按用户要求调整**：① 先加固 M1 验收清单（只写断言）② 再加载 6 个 skill 填留痕 ③ 登记入口模块 ④ 才写代码。

### 2026-10-30：M1 规格先行（只写断言，不写代码）
- 更新位置：`docs/acceptance/M1-harness-core.md`、`tests/test_offline_order.py`
- 更新内容：M1 验收清单从 7 条"位置类"断言重写为分组表。**该版实际 27 条（不是 26），且格式重排时丢掉了 3 条终止类断言**，见下条修正记录。
- 为什么先写断言：M1 是唯一跨格子的里程碑，清单就是规格书。M0 已经付过一次"先做、再发现尺子不对"的代价（一整轮返工），这次先把尺子定死。
- 与预期的偏差：原清单里 `coding-agent-harness` 一条断言都没有，实际是"声明了 skill 却没测"。已补执行边界（sandbox root / 只读路径 / 审批 / 验收基线声明）4 条。
- 验证：登记拦截实测有效——新建 `src/aide/llm/client.py` 后测试报 `assert not {'aide.llm.client'}`，删除后 `8 passed, 1 skipped`。
- 下一项：② 加载 6 个 skill 并填留痕。

### 2026-10-30：加载 6 个 skill，清单按 skill 修正（32 → 45）
- 更新位置：`docs/acceptance/M1-harness-core.md`、`Makefile`、`docs/status.md`
- **加载**：`context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery`、`coding-agent-harness`，全部用 `use_skill` 正式加载并逐条摘出 20 条可落地结论，写入 M1 的 Skill 留痕段。
- **发现 2 处口径错误（skill 直接纠正，不是我的判断）**：
  ① D 组四层故障原写"传输 / 协议 / 工具 / 语义"，skill 明确是 **API 层 / 工具层 / 上下文层 / 控制流层**；
  ② C 组压缩分层原写"截断 → 摘要 → 子 Agent 隔离"，但 skill 里**隔离是"优于压缩的替代方案"，不是最重的一层**，真正的五层是"预算控制 → 删噪声 → API 微压缩 → 归档摘要 → 全量压缩"。
- **净增 13 条断言（32 → 45）**，每条都能指回一条 skill 结论：工具定义走顶层 `tools` 不得混入 messages、会话恢复字节一致、历史思考回传按厂商契约、状态栏不得被外部内容污染、批量压缩 + `[COMPRESSED]` 标记、摘要必保四类、不可重试错误不得重试、指纹去重、空闲看门狗、错误路径禁止再调 LLM、压缩熔断 3 次、反馈信号结构化、回退可用性、并行故障隔离。
- **三条裁决**：① 状态栏默认**实现一（每轮替换）**，因含 TODO 与错误摘要属"较大且更新频繁"，符合 skill 的选择规则；保留 `AIDE_STATE_BAR_MODE` 开关以便实测两模式成本曲线。② 压缩阈值写死 **80%**；③ 熔断阈值写死 **3 次**，并注明来源是 Claude Code 生产统计，不是拍脑袋。
- **又一处工具坑（已修）**：`check-spec-diff` 的 grep 用的是 `test_[a-z_]+`，不含数字，于是 `test_compression_circuit_breaker_after_3_failures` 被截断成 `..._after_`，集合比对里出现一个"幽灵名字"。已改为 `test_[a-z0-9_]+`。这属于同一类：**工具本身静默给出错误结果**。
- 下一项：③ 登记入口模块（随后写代码）。

### 2026-10-30：M1 划阶段边界 + 每条断言标 skill 行号（45 → M1 可执行 43）
- 更新位置：`docs/acceptance/M1-harness-core.md`、`M2-tools-mcp.md`、`M4-apps-events.md`、`AGENTS.md`、`docs/status.md`
- **第八种失效形态（规格来自先验）**：D 组四层故障原本写"传输/协议/工具/语义"——格式正确、术语专业、逻辑自洽，**通过了所有下游检查**，因为下游查的是"实现是否符合规格"，不是"规格是否符合现实"。只有对照 skill 原文（error-recovery:19-24）才发现是编的。前七种都是"检查出了问题"，这一种是**检查的对象本身不存在**。
- **堵法**：每条断言加「出处」列，标 `skill:行号`；无原文支撑的显式标 **自定**。M1 的 45 条中 33 条有行号、12 条为自定，其中 `test_policy_denies_symlink_escape` 完全无原文支撑，列入下次审查优先复核。已写进 `AGENTS.md` 作为硬约束。
- **E 组降级为 policy 纯函数（复用 verifier 的同一手法）**：`core/policy.py` 改成 `evaluate(action) -> allow|deny|need_approval` 的纯函数，M1 只测函数、不执行真实操作；M2 接工具时执行器调用同一个 policy。5 条系统级断言（sandbox root / 执行层审批 / 反馈信号 / 回退可用 / 并行隔离）移到 M2，M1 留 5 条纯函数版。手法与 `verify(artifact, baseline)` 一致：**把对系统的依赖改成对参数的依赖**。
- **A 组 session_restore 降级**：M1 改为 `test_assembly_is_deterministic`（同进程内重建一致），跨进程逐字节恢复移到 M4。任务注册基线声明同样移到 M4。
- **计数**：M1 可执行 43（A8+B6+C6+D11+E5+F2+G5），外移 7 条；`check-spec-diff` 实测「断言条数 43 / 全文去重 50」——差值 7 正是外移条目，工具的口径分离在这里起了作用。
- 下一项：③ 登记入口模块（随后写代码）。

### 2026-10-30：补 2 条出处行号，出处覆盖做成机器检查
- 更新位置：`docs/acceptance/M1-harness-core.md`、`Makefile`、`docs/status.md`
- **违反自定规则 2 条（用户按"既无行号也未标自定 = 不通过"查出）**：`test_completion_claim_requires_verifier`、`test_verifier_rejects_obviously_bad_output` 只写了 `loop-engineering`，没有行号。补为 `loop-engineering:17,19` 与 `loop-engineering:18`（已 grep 核对原文确实在那三行）。
- **统计错误**：此前写"33 条有行号 / 12 条自定"，两者相加 45 ≠ 43，本身就不平。实际为 33/8/2，补完行号后 **43 条 = 35 有行号 + 8 自定 + 0 缺失**。
- **审查建议本身也要过同一道检验**：违规的那两条，正是上一轮审查提出的建议加进去的。建议进入清单时不会自动获得豁免。
- **新增 `make check-provenance`**：限定在自动检查节内，统计有行号 / 标自定 / 缺失，有缺失即打印具体行并 exit 1。与 `check-spec-diff` 成对——**一个查"有没有少"，一个查"有没有编"**。正反两向实测：正常 `43/35/8/完整`；临时删一处出处后报错并打印该行、exit 1。
- **假阳性教训（与前面几次方向相反）**：统计时若用全文 `grep -E '^\| \`test_'`，会同时匹配断言表 43 行 + 依赖表 9 行 + 外移表 7 行，得出"22 条无出处"的错误结论——后两张表本来就没有出处列。这不是漏报而是**凭空报出一个不存在的问题**，比漏报更坏：会让人去排查不存在的问题，而报告者显得"很有发现"。规则：**新加的检查第一次跑出异常时，先怀疑工具，再怀疑数据**。

### 2026-10-30：M1 A 组实现（先建测试基础设施，再写 provider）
- 更新位置：`tests/fakes.py`、`tests/test_context_prefix.py`、`src/aide/core/{message,context}.py`、`pyproject.toml`、`Makefile`、`tests/test_offline_order.py`
- **`tests/fakes.py` 是 M1 的第一个文件**：`FakeProvider(script)` 按轮次返回、用尽后重复最后一条；`fail_after(n, error)` 注入 429/超时/流中断；`recorded` 保存完整请求体供前缀字节断言。43 条里约 20 条需要控制模型输出，没有它这些断言只能写成"看起来测了"。
- **按运行成本打 marker**（不是按知识归属）：`unit`（纯函数与替身）/ `contract`（需要真实厂商 API）/ `slow`（需要加载模型）。`test_reasoning_echo_matches_provider_contract` 单独归 `contract` 并默认跳过——**FakeProvider 能验「我们怎么发」，验不了「对方怎么收」**，R1 剥离 vs V4/K2 必须回传只有真 API 才知道。新增 `make test-unit` / `make test-contract`。
- **A 组产物是纯函数**：`assemble_context(prefix, trajectory, state_bar) -> list[Msg]`，不读时钟、不读环境、不依赖调用次数。`state_bar` 是参数不是内部生成，因此"状态栏进不了前缀"是类型层面的保证，不靠自觉。`Prefix.canonical_bytes()` 提供前缀的唯一权威字节表示，断言比较 digest 而非对象。
- **A 组 8 条断言全部落地**：前缀 hash 10 轮一致、前缀无易变数据（7 条正则黑名单）、工具定义不进 messages、工具定义内容与顺序不变、角色合法且无自拼接格式、段序位置断言、同输入同字节、轨迹含 system 被拒（额外加的防御断言）。`contract` 那条按设计跳过。
- **离线守卫发现规则过粗并已修正**：新模块 `aide.core.*` 只依赖标准库，却被判为"未应用离线策略"。改为 **SDK 感知**：子进程对比导入前后的 `sys.modules`，只有加载了 openai/httpx/transformers/torch/huggingface_hub 才要求离线；纯标准库模块跳过。正反实测：无 `_offline` 的 httpx 模块 exit **11**，有 `_offline` 的 exit **0**。
- **计数**：`18 passed, 2 skipped`；`make test-unit` 为 `18 passed, 1 skipped, 1 deselected`；`check-spec-diff` 43；`check-provenance` 完整。
- 下一项：等用户按三条审查口复核 A 组（fakes 优先、marker 分档、纯函数），通过后接 B 组状态栏。

### 2026-10-30：修依赖成环，check-spec-diff 改为可配置基线
- 更新位置：`docs/acceptance/M1-harness-core.md`、`Makefile`、`docs/status.md`
- **依赖成环（规则当场失效）**：`test_verifier_rejects_obviously_bad_output` 依赖 E 组"任务声明验收基线"，而全局顺序是 G 在 E 之前。走到 G 时 E 还没做，只剩"临时跳过"或"放宽判定"两条路——正是规则要防的两种。
- **解法（选轻的那种）**：把基线从"任务注册表"下移到**函数参数**，verifier 签名固定为 `verify(artifact, baseline)`，测试自带最小基线（文件非空 + 语法可解析）。G 组因此自足，与 E 解耦；E 只保留产品约束"任务上线必须声明基线"。依赖表已移除该行并写明原因。
- **check-spec-diff 三个修正**：① `BASE ?= HEAD`——默认比"工作区 vs 最新提交"，正是"这次改动有没有丢"要问的问题，查历史用 `BASE=HEAD~3`；② 基线为空直接报错退出，堵住"SPEC 写错时输出仍是空、看起来正常"；③ 条数只数**自动检查节内**的表格行。
- **自己又踩了一次口径混用**：条数首版写成全文 `grep -c`，把依赖表的 9 行也算进去，报 41；改为 `awk '/## 三、自动检查/,/## 三之二/'` 限定范围后为 32。这是本轮第二次"计数看起来对、实际混了两个集合"。
- 下一项：② 加载 6 个 skill 并填留痕。

### 2026-10-30：M1 清单加验证器配对断言与依赖图（31 → 32）
- 更新位置：`docs/acceptance/M1-harness-core.md`、`Makefile`、`docs/status.md`
- **新增 `test_verifier_rejects_obviously_bad_output`**：构造空文件 / 语法错误的脚本 / 只改注释的补丁，断言 verifier 返回 False 并给出可定位原因。理由：`test_completion_claim_requires_verifier` 只证明「verifier 在环内」，一个永远返回 True 的空实现同样能通过；必须有一条证明「verifier 有区分力」。G 组因此改名为「预算、终止与验证」，两条件必须成对，且**必须在写 verifier 之前定稿**，否则会退化成"实现写成什么样、断言就迁就成什么样"。
- **新增「三之二、实现顺序与依赖（施工图）」**：给出全局顺序 A→B→G→C→D→E→F，并列出 10 条有前置依赖的断言及其依赖项与原因（如 `test_state_bar_survives_compression` 依赖状态栏+压缩两样都在）。附规则：后做的功能**不得顺手放宽**前面的断言，测不了就标 `xfail` 写明解除条件，不得删除或改软。
- **新增 `make check-spec-diff`**：对比上一版验收清单的断言集合，输出"旧有今无 / 今有旧无 / 当前条数"。注意 `comm` 要求输入已排序，漏 `sort -u` 会静默给出错误结果——这类工具坑本身也是静默失效，故固化成目标而非靠手敲。实测输出 `当前条数：32`。
- 与预期的偏差：上一版把 `test_verifier_rejects_obviously_bad_output` 这类"区分力"断言漏掉了，只留下"调用过"的断言；这属于**断言强度不足**，比数量缺失更难发现。
- 下一项：② 加载 6 个 skill 并填留痕。

### 2026-10-30：M1 清单回归修正（补回 3 条终止断言，27 → 31）
- 更新位置：`docs/acceptance/M1-harness-core.md`、`AGENTS.md`、`docs/status.md`
- **回归事实**：上一版重排时丢掉 3 条断言——`test_turn_limit_terminates_with_reason`、`test_budget_limit_terminates`、`test_timeout_limit_terminates`。用 `comm -23` 对比 `933776f` 与 `569dfe1` 确认；同时 `test_state_bar_appended_not_prepended`、`test_offline_env_set_after/sdk_import` 两条是**改名**（非删除）。
- **危害**：M1 内部自相矛盾——子模块表写"三重闸门 + 终止判定"、命令预期写"以轮次耗尽结构化终止原因退出"，而断言表 0 条；拒绝条件里"模型自称完成即退出"也没有对应断言。这三条恰是 `loop-engineering` 关于预算与防失控的全部内容。
- **修正**：新增 G 组「预算与终止」4 条（3 条补回 + 1 条 `test_completion_claim_requires_verifier` 补上拒绝条件与断言表的口径差）。总数 **31 = A5 + B5 + C5 + D6 + E4 + F2 + G4**。
- **计数纠正**：上一版报"26 条"是错的，实际为 27（把 F 组原有的离线断言当成了新增）。
- **机制补丁**：`AGENTS.md` 新增硬约束——修改验收文档必须 `git diff` 自查，删除或改名任何断言都要在提交信息里写明理由。**格式重排尤其危险，因为它看起来不像"删"**。
- 下一项：② 加载 6 个 skill 并填留痕。
