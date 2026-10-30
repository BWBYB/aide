# M8 验收：交付

**落在哪个格子**：元层（证据与文档）
**书中依据**：书仓库自身的做法——每章 `EXPERIMENT_LEDGER.md` 记运行条件与服务可用性；`FAILURE_ANALYSIS.v1.md` 逐题归因；旧 RAG 项目沿用的"更新位置 / 更新内容 / 与预期的偏差 / 下一项"模板
**skill**：`project-docs-maintainer`

## 一、四份必交材料

| 材料 | 内容 | 为什么是它 |
|---|---|---|
| `docs/architecture.md` | 分层图、数据流、模块边界 | 面试讲架构的底稿 |
| `docs/harness-decisions.md` | 每条取舍三栏：动机 / 被否决方案 / 实测依据 | **面试核心材料**，防止"跟着教程做"的印象 |
| `docs/eval/*.md` | 检索层漂移 + 任务层 baseline + 进化日志 | 有数字才叫评估 |
| `docs/demo.md` | 5 分钟演示脚本，含失败演示 | 只会演成功案例等于没演 |

## 二、命令

```bash
make report        # 由 evals/runs 自动生成报告，禁止手写数字
make doctor        # 检查所有文档里的数字能否追溯到 runs 文件
```

## 三、自动检查（必须绿）

- `test_every_number_traceable` —— 文档中出现的指标必须能在 `evals/runs/` 找到来源
- `test_harness_decisions_have_three_columns` —— 每条取舍三栏齐全
- `test_no_unverified_claim` —— 未验证结论必须显式标"未验证"
- `test_upstream_untouched` —— 两个上游仓库文件的 sha256 与初始快照一致

## 四、人工判断

1. **报告里有没有"我们没验证"的段落？** 没有的话说明在夸海口。
2. **`harness-decisions.md` 的"被否决方案"是真考虑过的，还是编的？** 编的会经不起追问。
3. **演示脚本里有没有失败案例？** 只有成功案例的演示不可信。

## Skill 留痕（必填，空着即未完成）

- 加载：`project-docs-maintainer`（非书内 skill）
- 采纳结论：
- 冲突与裁决：
- 实测差异：

## 五、拒绝条件

- 报告数字手写，无法追溯到 runs。
- 用"效果显著提升"这类无口径表述。
- 演示脚本只展示 happy path。
- 简历里写"自研 Agent 框架"但仓库无法 30 秒跑通。
