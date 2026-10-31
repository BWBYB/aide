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
- **改验收文档必须自查**：修改任何 `docs/acceptance/Mx-*.md` 后必须 `git diff` 复核；
  **删除或改名任何一条断言，都要在提交信息里写明理由**。断言表是规格，
  静默删条目等于静默降标——格式重排尤其危险，因为它看起来不像"删"。
- **断言必须标出处**：每条断言都要标注 `skill:行号` 或书稿章节；没有原文支撑的，
  必须显式标 **自定**。这条防的是"规格来自先验"：编出来的口径格式正确、术语专业、
  逻辑自洽，能通过所有下游检查——因为下游检查的是"实现是否符合规格"，
  不是"规格是否符合现实"。只有对照原文才能发现它是编的。
- **替身测试的三分法**：用替身/切片模拟真实行为的测试，只有两种合法状态——
  ① 同轮已实现 → 改真实调用；② 尚未实现 → `xfail(strict=True)` + reason 写解除条件。
  **不允许第三种：用替身模拟却毫无标记**。第三种就是"名字承诺 A、实际测 B"的形态
  （典型：`test_state_bar_survives_compression` 曾用 `trajectory[-2:]` 切片冒充压缩）。

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

1. **开工前加载**：每个里程碑动代码之前，用 `make skill-load SKILLS="a b" MILESTONE=Mx` 加载该里程碑对应的全部 skill（清单见 `docs/skills-plan.md`）。判定标准是它会在 `docs/skills-load.log` 追加带 sha256 的一行——**手工写「已加载」不算加载**。不允许用 `read_file` 读 `SKILL.md` 代替加载。
2. **结论要落地**：在该里程碑的验收清单里填写「Skill 留痕」段，写明：加载了哪些 skill、采纳了哪条具体结论、哪条结论与本步做法冲突以及如何裁决。
3. **空着即未完成**：该段未填写的里程碑，只能停在 `待验收`，不得进入 `完成`。
4. **冲突以书稿为准**：skill 结论与本步实现冲突时，默认改实现；确实要偏离时在 `docs/adr/` 记一条，写明动机与验证条件。
5. **明确不用的要写明理由**：`computer-use`、`post-training-strategy`、`reward-design`、`bad-case-to-dpo`、`multi-agent-design` 属于本项目有意不做，理由见 `docs/skills-plan.md`，不得因为"没用上"而含糊过去。

## 七、离线前置（import 时序）

任何入口模块（脚本、CLI、服务启动、测试 conftest）**必须在导入第三方库之前**先
`import knowledge._offline`，或经由 `knowledge.loader` 间接生效（`knowledge/_offline.py` 是唯一实现处）。

原因：`huggingface_hub` 在**导入期**读取 `HF_HUB_OFFLINE` 常量。设晚了不报错，只会让每次调用
静默尝试联网直到超时——比崩溃更难查，是"静默失效"的第三种形态。

高危点：M1 的 LLM 客户端会直接 `import openai` / `import httpx`，一旦它先于 `knowledge.loader`
被导入，离线设置就晚了。

验收方式：M1 的测试断言 `os.environ["HF_HUB_OFFLINE"] == "1"`，且该断言在导入 SDK 之前成立。
