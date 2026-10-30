# M5 验收：任务层评估

**落在哪个格子**：验证
**书中依据**：ch7「Agent 的评估」——**评估的对象是"模型 + Harness"组合体，不是模型**
**skill**：`agent-evaluation`、`eval-dataset-design`
**参考实现**：`chapter7/tau2-bench-eval/`、`chapter7/user-memory-system-evaluation/`、`chapter7/agent-cost-analysis/`

## 一、与 M0 的分工

- M0 评估的是**检索层**（Hit@5 / MRR / nDCG，ranx 口径）
- M5 评估的是**任务层**（pass@1 / Pass^3 / 单位任务成本 / P95 延迟）

两者不能混在一个数字里。检索分高不代表任务成功，这是 ch7 反复强调的。

## 二、指标口径（必须先写死，再跑）

| 指标 | 口径 | 用在哪 |
|---|---|---|
| pass@1 | 单次运行通过率 | 日常回归 |
| Pass^3 | 连续 3 次全部通过 | 稳定性（个人助手该看这个） |
| 单位任务成本 | 总成本 / 任务数，输入/输出/缓存 token 分开计价 | 成本护栏 |
| P95 延迟 | 尾部延迟，不看均值 | 体验 |
| 失败归因分布 | 按首错步类别聚类 | 下一轮改进输入 |

> skill 里的硬约束：100 用例、成功率 70% 时 95% 置信区间约 ±9 个百分点。
> 40 条任务的分数差在 5% 以内**不能宣称提升**。

## 三、命令

```bash
make eval
# 预期：运行 40+ 任务，落 evals/runs/<timestamp>/*.json
#       并生成 docs/eval/task-baseline.md

make eval-diff BASE=<run_id> CAND=<run_id>
# 预期：配对比较（McNemar 或配对 bootstrap），输出显著性，不给裸差值
```

## 四、自动检查（必须绿）

- `test_every_task_has_verifier` —— 没有验证器的任务不允许进评估集
- `test_verifier_preferred_over_judge` —— 能确定性判定的任务不允许走 LLM 判官
- `test_report_includes_confidence_interval` —— 报告缺置信区间即测试失败
- `test_report_flags_small_delta_as_inconclusive` —— 分差小于噪声时必须标"不足以下结论"
- `test_judge_position_bias_mitigated` —— 配对比较做位置交换，抵消位置偏置
- `test_eval_isolated_from_dev_set` —— 评估集与调参集不重叠

## 五、人工判断

1. **随机抽 3 条失败轨迹，归因是否指到"首个导致偏离的错误步"？** 指到最后的报错就是错的——那只是连锁反应。
2. **有没有哪条任务是"永远通过"的？** 永远通过说明没有区分度，应替换。
3. **判官给分与回答长度是否正相关？** 相关说明被长度偏差带偏了。

## Skill 留痕（必填，空着即未完成）

- 加载：`agent-evaluation`、`eval-dataset-design`
- 采纳结论：（逐条：指标口径 / Pass@k 与 Pass^k 选择 / 验证器优先 / 置信区间 / 陷阱任务设计）
- 冲突与裁决：
- 实测差异：

## 六、拒绝条件

- 报告里出现"提升 X%"但没有置信区间或配对分析。
- 用 4/4 通过写成"系统 100% 可靠"。
- 判官与 Agent 同家族模型，且未做交叉评判。
- 评估集被用来调参后又当作测试集汇报。
- 失败归因写成"模型能力不足"这类无法着手修改的结论。
