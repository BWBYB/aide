# 验收总纲：每一步对应 Agent 的哪个模块

本文件是全部里程碑的入口。规则只有一条：**没有你逐条勾选，就没有"完成"。**

## 一、坐标系（来自书稿第 1 章）

> Agent = LLM + 上下文 + 工具
> 生产形态：Agent = Model + Harness，`Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正`

所以只有 6 个格子，没有第 7 个。每个里程碑必须落在某个格子上；落不上的，就是多余的工程。

## 二、模块映射总表

| 里程碑 | 落在哪个格子 | 书中依据 | 对应 skill | 验收清单 |
|---|---|---|---|---|
| M0 知识层扩容 | **上下文**（外部知识来源） | ch3 用户记忆和知识库 | `rag-pipeline` | [M0](M0-corpus-v2.md) |
| M1 Harness 内核 | **LLM + 上下文 + 约束** | ch1、ch2（全书最关键）、ch5 故障恢复 | `context-engineering`、`kv-cache-design`、`agent-state-bar`、`context-compression`、`error-recovery` | [M1](M1-harness-core.md) |
| M2 工具层 | **工具** | ch4 工具是 Agent 的双手 | `tool-design`、`tool-discovery`、`mcp-skill-hub` | [M2](M2-tools-mcp.md) |
| M3 记忆系统 | **上下文**（跨会话持久化） | ch3 前半 用户记忆 | `memory-system` | [M3](M3-memory-system.md) |
| M4 任务应用与事件 | **LLM 策略层 + 时序维度** | ch6 交互：观察与动作空间的扩展 | `async-event-agent` | [M4](M4-apps-events.md) |
| M5 任务层评估 | **验证** | ch7 把表现变成可比较信号 | `agent-evaluation`、`eval-dataset-design` | [M5](M5-eval-task.md) |
| M6 进化闭环 | **纠正** | ch9 Agent 的持续进化 | `agent-evolution`、`loop-engineering` | [M6](M6-evolution.md) |
| M7 控制台 | **可观测性**（非 Agent 模块） | ch7 可观测性小节 | `frontend-ui-engineering` | [M7](M7-console.md) |
| M8 交付 | **元层**（证据与文档） | ch7 失败归因 + 各章 EXPERIMENT_LEDGER 惯例 | `project-docs-maintainer` | [M8](M8-delivery.md) |

## 三、有意不做的模块

| 没做 | 属于哪个格子 | 理由 |
|---|---|---|
| 多 Agent 协作（ch10） | LLM 策略层 | ch10 论断：多 Agent 的每个设计决策都能在单 Agent 三要素中找到对应。等 M5 证明单 Agent 不足再引入 |
| 语音 / Computer Use（ch6 另两维） | 观察与动作空间 | 需额外模型能力与系统依赖，与个人助手主线收益不匹配 |
| SFT / RL 后训练（ch8） | 模型参数 | ch9 四种载体里唯一不做的，3 个月投入产出比最低 |
| RAG 代码迁移重构 | 上下文 | 用户已明确"暂时不动，需要时再讨论" |

## 四、验收清单的五段结构

每份 `Mx-*.md` 固定五段：

1. **命令**：可复制执行，附成功与失败两种预期输出
2. **自动检查**：必须绿的测试用例（贴真实输出，不接受口述）
3. **人工判断**：程序验不了的 1–3 项，需要你亲自看
4. **Skill 留痕**：加载了哪些 skill、采纳了哪条结论、冲突如何裁决（**空着即未完成**，见 `AGENTS.md` 第六节）
5. **拒绝条件**：明确哪些情况算不通过

## 五、状态流转

```
pending → in_progress → 待验收 → 完成
```

`docs/status.md` 由 AI 维护前三个状态，**只有你能把它改成"完成"**。
