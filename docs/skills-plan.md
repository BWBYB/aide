# Skill 使用计划（22 个）

书仓库 `skills/` 的 22 个 skill 已全部安装到 `~/.skills-manager/skills`（本机共 47 个 skill，这 22 个是书的那套）。

**规则**：每个里程碑动代码前用 `use_skill` 加载对应 skill，并把采纳的结论写进验收清单的「Skill 留痕」段。空着即未完成（见 `AGENTS.md` 第六节）。

## 一、按里程碑分配（16 个）

| 里程碑 | 要加载的 skill | 用它决定什么 |
|---|---|---|
| M0 | `rag-pipeline` | 分块参数、混合检索三阶段、recall@k/MRR/nDCG 口径，判断扩容后 Dense 与 BM25 的胜负变化 |
| M1 | `context-engineering` | 静态前缀 + 轨迹 + 状态栏的上下文结构，系统提示词骨架 |
| M1 | `kv-cache-design` | 前缀不变性、三种破坏缓存的写法、缓存边界 |
| M1 | `agent-state-bar` | 状态栏五项技术与每轮替换策略 |
| M1 | `context-compression` | 压缩触发阈值与可溯源索引保留 |
| M1 | `error-recovery` | 四层故障分类、熔断上限、跨厂商接管 |
| M1 | `coding-agent-harness` | Harness 四组件、执行边界与验收基线（原方案漏排，补入） |
| M2 | `tool-design` | 工具分类、ACI 原则、感知/执行两类工具的不同截断与审批策略 |
| M2 | `tool-discovery` | 两级语义路由与渐进式披露，避免破坏 KV Cache |
| M2 | `mcp-skill-hub` | MCP vs Skill 的选型、工具描述投毒与同名遮蔽审查 |
| M3 | `memory-system` | 四种存储格式选型、冲突处理、遗忘、记忆与 RAG 的边界 |
| M4 | `async-event-agent` | 事件循环与安全点、三类事件触发、取消/抢占策略 |
| M5 | `agent-evaluation` | 指标口径、验证器与判官边界、失败归因、显著性 |
| M5 | `eval-dataset-design` | 40+ 任务评估集的构成、陷阱任务、防泄漏 |
| M6 | `agent-evolution` | 四种更新载体的适用边界、灰度与回滚 |
| M6 | `loop-engineering` | 过早宣称完成的三种形态、验证器与终止条件 |

## 二、备选（1 个）

| skill | 何时启用 | 当前不用的理由 |
|---|---|---|
| `knowledge-org` | 语料需要 RAPTOR/GraphRAG 级结构化索引，或扁平检索 recall 长期上不去时 | 当前 567 chunks，FTS5 + 向量 + RRF 已够；过早引入结构化索引会让评估无法归因 |

## 三、明确不做（5 个）

| skill | 对应模块 | 不做的理由 |
|---|---|---|
| `computer-use` | ch6 观察/动作空间 | 需要额外模型能力与系统依赖；个人助手的收益不匹配成本 |
| `post-training-strategy` | 模型参数 | 3 个月周期内投入产出比最低；ch9 四种载体里唯一不碰的 |
| `reward-design` | ch8 奖励函数 | 不做 RL，无需奖励设计 |
| `bad-case-to-dpo` | ch8 训练数据 | 不做后训练，bad case 走 M6 的 prompt/skill/程序改进而非 DPO |
| `multi-agent-design` | 协作拓扑 | ch10 论断：多 Agent 每个决策都能在单 Agent 三要素中找到对应。等 M5 证明单 Agent 不足再单独立项 |

## 四、非书内 skill（借用通用库）

| skill | 用在哪 | 说明 |
|---|---|---|
| `frontend-ui-engineering` | M7 | 书里没有前端章节，属工程实现 |
| `project-docs-maintainer` | M8 | 文档维护与漂移检查 |
