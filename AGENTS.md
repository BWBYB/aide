# AGENTS.md —— 项目约定与禁区

给任何在此仓库工作的编码 Agent 的硬性约定。违反即视为未完成。

## 一、方法论来源唯一

本项目的每个设计决策必须以以下两者为准：

1. `/Users/Admin/Documents/ai-agent-book/book/*.md` 正文结论；
2. `/Users/Admin/Documents/ai-agent-book/skills/*/SKILL.md` 的判断规则与常见陷阱。

开工前先加载对应 skill，再读对应章节。若实现与书稿结论冲突，**默认以书稿为准**；确实需要偏离时，必须在 `docs/adr/` 新增一条记录，写明"为什么偏离、验证条件是什么"。

## 二、禁止修改的上游（只读）

| 路径 | 关系 |
|---|---|
| `/Users/Admin/Documents/ai-agent-book` | 方法论与书稿语料来源 |
| `~/Documents/ChatGPT/个人网站` | 旧 RAG 实现与个人资料来源 |

两者任何文件都不得被本仓库修改、删除或重排。需要数据时通过环境变量 `BOOK_HOME` / `AIDE_RAG_HOME` / `PERSONAL_HOME` 读取。

## 三、不得自称完成

- 一个里程碑只有在其 `docs/acceptance/Mx-*.md` 清单被**用户逐条勾选**后，才能把状态改为 `完成`。
- 未经用户勾选，`docs/status.md` 中该里程碑只能标 `待验收`。
- 禁止在没有运行命令的情况下声称"已通过测试"。测试结果必须贴出真实输出。

## 四、偏差必须留证

每个里程碑结束时追加一条记录，格式固定：

```
### YYYY-MM-DD：Mx 名称
- 更新位置：
- 更新内容：
- 与预期的偏差：（不允许只写"无"，需给出证据或命令输出）
- 下一项：
```

## 五、代码约束

- Python 3.11+，全部使用类型标注；配置走 `pydantic-settings`，密钥只从环境变量读。
- 不得把 API Key、`.env`、`artifacts/`、`traces/`、`evals/runs/` 提交入库。
- 知识层通过 `knowledge/loader.py` 桥接旧包，不得在业务代码里散落 `sys.path` 操作。
- 上下文静态前缀必须字节稳定；新增动态信息只能追加在轨迹尾部（见 `docs/adr/0003-context-layout.md`）。
- 单文件不超过 300 行。

## 六、Skill 使用留痕（硬约束）

书仓库的 22 个 skill 是本项目的方法论来源之一，**必须真的加载，不能只在计划里写名字**。

规则：

1. **开工前加载**：每个里程碑动代码之前，先用 `use_skill` 加载该里程碑对应的全部 skill（清单见 `docs/skills-plan.md`）。不允许用 `read_file` 读 `SKILL.md` 代替加载。
2. **结论要落地**：在该里程碑的验收清单里填写「Skill 留痕」段，写明：加载了哪些 skill、采纳了哪条具体结论、哪条结论与本步做法冲突以及如何裁决。
3. **空着即未完成**：该段未填写的里程碑，只能停在 `待验收`，不得进入 `完成`。
4. **冲突以书稿为准**：skill 结论与本步实现冲突时，默认改实现；确实要偏离时在 `docs/adr/` 记一条，写明动机与验证条件。
5. **明确不用的要写明理由**：`computer-use`、`post-training-strategy`、`reward-design`、`bad-case-to-dpo`、`multi-agent-design` 属于本项目有意不做，理由见 `docs/skills-plan.md`，不得因为"没用上"而含糊过去。
