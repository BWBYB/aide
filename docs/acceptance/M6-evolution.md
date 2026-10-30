# M6 验收：进化闭环

**落在哪个格子**：纠正
**书中依据**：ch9「Agent 的持续进化」——学习信号三层（环境结果 / 过程规则 / LLM Rubric）× 更新载体四种（知识 / 指令 / 程序 / 参数）
**skill**：`agent-evolution`、`loop-engineering`
**参考实现**：`chapter9/trajectory-verifier/`、`self-evolving-tools/`、`prompt-auto-optimization/`、`harness-safety-gate/`、`self-evolution-eval/`

## 一、只做三种载体

| 载体 | 做不做 | 理由 |
|---|---|---|
| 知识（记忆 / 索引） | 做 | M3、M0 已具备写入路径 |
| 指令（prompt / skill 文本） | 做 | 可 diff、可回滚 |
| 程序（工具脚本） | 做 | 可单测 |
| 参数（SFT / RL） | **不做** | ch8 后训练在 3 个月周期内投入产出比最低 |

## 二、命令

```bash
make evolve-cluster
# 预期：从 evals/runs 的失败轨迹聚类出根因，按根因分组输出

make evolve-propose ROOT=<root_cause_id>
# 预期：生成候选改进 diff（prompt / skill / 脚本三类），落 artifacts/evolution/<id>/

make evolve-gate CAND=<candidate_id>
# 预期：小样本回归，通过则进入灰度；不通过则拒绝并记录原因

make evolve-rollback VERSION=<version>
# 预期：恢复到指定版本，并打印回滚前后指标
```

## 三、自动检查（必须绿）

- `test_candidate_must_have_diff` —— 无 diff 的候选不允许提交
- `test_gate_blocks_regression` —— 小样本回归退化时门禁拒绝放量
- `test_rollback_restores_metrics` —— 回滚后指标恢复，且两次指标都入库
- `test_signal_uses_first_error_step` —— 信号提取取首错步，不取最后一条报错
- `test_no_param_change` —— 进化流程不得写模型权重或调用训练接口

## 四、人工判断（这是最后一道闸门）

1. **这个改进是"真修了根因"还是"绕过了评估集"？** 例如把某条失败任务的措辞硬编码进 prompt，指标上去了但能力没变——这是 reward hacking。
2. **改进后的行为，换一个同类任务是否也变好？** 只对单题有效说明过拟合。
3. **灰度期间有没有出现"评价变好但体验变差"？** 例如回答变短、拒答变多。

## Skill 留痕（必填，空着即未完成）

- 加载：`agent-evolution`、`loop-engineering`
- 采纳结论：（逐条：更新载体选择 / 信号三层 / 灰度门禁 / 终止条件与验证器挂钩）
- 冲突与裁决：
- 实测差异：

## 五、拒绝条件

- 候选改进无 diff、无回滚快照。
- 直接让 LLM 改自己的提示词后立刻全量生效。
- 指标提升来自评估集泄漏（把 gold 答案写进 prompt）。
- 进化后失败归因分布没有变化，说明只是换了个说法。
- 碰到模型参数训练。
