# M3 验收：记忆系统

**落在哪个格子**：上下文（跨会话持久化）
**书中依据**：ch3 前半「用户记忆系统」——四种存储格式、三层次能力框架、冲突与遗忘
**skill**：`memory-system`
**参考实现**：`chapter3/user-memory/`（四种格式可切换）、`chapter3/user-memory-evaluation/`（L1/L2/L3 各 20 用例、LLM-as-Judge）

## 一、与 M0 的边界（必须守住）

`memory-system` skill 的判断规则：

> **单用户的个性化信息**（偏好、身份、关系、历史）走用户记忆；
> **面向所有用户共享的集体知识**（书稿、文档）走 RAG 知识库。

所以"我喜欢什么样的简历措辞"进记忆，"书稿第 4 章讲了什么"进知识库。混淆即不通过。

## 二、命令

```bash
make memory-eval
# 预期：分别输出 L1 / L2 / L3 三层通过率，以及每层的失败用例 ID

make memory-list --kind preference
# 预期：列出偏好类记忆卡片，每张带 value / backstory / source_trace / confidence / last_hit_at

make memory-conflict
# 预期：列出待确认冲突队列，可执行 merge / negate / downweight
```

## 三、自动检查（必须绿）

- `test_extraction_is_selective` —— "搜索结果返回 3 个选项"这类短期细节不入库
- `test_extraction_is_abstracted` —— 单次"靠窗座位"归纳为长期偏好，而不是原句存档
- `test_conflict_goes_to_queue_not_overwrite` —— 先"住北京"后"搬到上海"两条并存，进待确认队列，带时间上下文
- `test_dedup_merge_keeps_history` —— 合并后旧卡片 `superseded_by` 指向新卡，不物理删除
- `test_forgetting_uses_decay_and_refcount` —— 遗忘走时间衰减 + 引用计数，不是简单删最旧
- `test_backstory_required_for_ambiguous_entity` —— 同名实体（"张医生"）缺 backstory 时拒绝入库
- `test_memory_retrieval_hybrid` —— FTS5 + 向量 + RRF 走同一检索门面

## 四、人工判断

1. **抽 5 张记忆卡，`source_trace` 是否真能点回原始对话的那句话？** 回溯不到就是编造的。
2. **冲突处理是不是"保留最新一条"？** 那是 skill 明确点名的陷阱。应追溯信息源，判断是否在不同时间/对象/条件下分别成立。
3. **记忆库是否在膨胀？** 跑 20 轮对话后看卡片数增长曲线，若线性增长说明缺少整理。

## Skill 留痕（必填，空着即未完成）

- 加载：`memory-system`
- 采纳结论：（逐条：格式选型 / 提取三规则 / 冲突处理 / 遗忘策略 / 记忆与 RAG 边界）
- 冲突与裁决：
- 实测差异：

## 五、拒绝条件

- 会话结束后逐句存档（违反选择性/抽象化/结构化三条规则）。
- 写入时激进 UPDATE/DELETE，一次误判即不可逆丢历史。
- 把轨迹当长期记忆直接检索（原始流水只增不改、噪声大）。
- 记忆生成后才能回答问题，但评估时 Agent 仍可回看原始对话（违反"仅能访问记忆"的评测条件）。
