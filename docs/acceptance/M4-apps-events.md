# M4 验收：任务应用与事件驱动

**落在哪个格子**：LLM 策略层 + 时序维度
**书中依据**：ch6「交互：观察与动作空间的扩展」——异步与事件驱动共享同一批系统原语：**唤醒、安全点、取消、抢占、快慢路径分离**
**skill**：`async-event-agent`
**参考实现**：`chapter6/async-agent/`、`chapter6/agent-with-event-trigger/`、`chapter6/astra-async-steering/`

## 一、五个任务应用分别验证什么

| 应用 | 验证 Harness 的哪个能力 |
|---|---|
| 每日学习简报 | 定时唤醒 + 多来源聚合（记忆 + 知识库 + 待办） |
| JD 匹配与简历改写建议 | 多步工具编排 + 确定性验收（要求项逐条对齐） |
| 个人知识库问答 | 必须先检索后回答，引用可溯源 |
| 周报复盘 | 从轨迹统计投入分布，验证轨迹可分析性 |
| 长期目标拆解 | 跨会话状态推进，验证记忆 + 业务状态分层 |

## 二、命令

```bash
make brief
# 预期：生成当日简报，落 traces/ 与 artifacts/briefs/<date>.json
#       简报里每条结论后面带来源（记忆卡 ID 或 chunk id）

make run TASK="把这个 JD 和我的简历对齐，列出缺口" JD=fixtures/jd-sample.md
# 预期：逐条要求项输出「满足 / 部分满足 / 缺口」，每条给证据

make events --simulate interrupt
# 预期：长任务在安全点被挂起，日志显示 interrupt_at_step，随后可 resume 续跑
```

## 三、自动检查（必须绿）

- `test_scheduler_triggers_once_per_cron_window` —— 同一窗口不重复触发
- `test_long_task_suspends_at_safe_point` —— 中断只在安全点发生，不会切断工具执行中途
- `test_resume_continues_from_checkpoint` —— 续跑从断点继续，不重放已完成步骤
- `test_cancel_is_not_rollback` —— 取消后的副作用被如实记录，不假装没发生
- `test_dead_letter_on_repeated_failure` —— 连续失败进死信队列，不无限重试
- `test_brief_every_claim_has_source` —— 简报中每条结论都有来源引用

## 四、人工判断

1. **五个任务里，至少有一个你自己会真的每天用。** 否则这个应用是编出来的，删掉。
2. **简报有没有"正确的废话"？** 比如"建议多学习"这类无信息量句子，属于失败。
3. **中断后恢复，用户看到的状态是否连贯？** 不能出现"我刚才说到哪了"。

## Skill 留痕（必填，空着即未完成）

- 加载：`async-event-agent`
- 采纳结论：（逐条：安全点位置 / 三类事件触发 / 取消与抢占语义 / 紧急度判定）
- 冲突与裁决：
- 实测差异：

## 五、拒绝条件

- 定时任务与手动任务共用同一上下文，导致状态污染。
- 长任务阻塞 HTTP 请求直到完成（没有后台执行与句柄）。
- 简报不含来源，无法回溯。
- 为了凑齐五个应用而硬造场景，却不接入真实数据源。
