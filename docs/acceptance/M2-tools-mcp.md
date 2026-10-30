# M2 验收：工具层

**落在哪个格子**：工具
**书中依据**：ch4「工具」——五类工具（感知 / 执行 / 协作 / 事件触发 / 用户沟通）；**感知工具控信息量，执行工具控风险**
**skill**：`tool-design`、`tool-discovery`、`mcp-skill-hub`
**参考实现**：`chapter4/perception-tools/`、`execution-tools/`、`active-tool-discovery/`、`chapter5/coding-agent/tools/`（16 工具，纯 Python Grep）

## 一、命令

```bash
make tools
# 预期：按「感知 / 执行 / 协作 / 事件 / 沟通」五类分栏列出工具
#       每个工具带 name / side_effect / requires_approval / 参数摘要

make tools-check TOOL=Write
# 预期：打印该工具 JSON Schema，并对一个非法参数返回校验错误（不执行）

make run TASK="把今天的学习笔记写入 notes/2026-10-30.md"
# 预期：Write 触发审批提示；未批准时返回"已取消"，文件不存在
```

## 二、自动检查（必须绿）

- `test_every_tool_declares_side_effect` —— 无 `side_effect` 的工具不允许注册
- `test_dangerous_tool_requires_approval` —— `write`/`exec` 类未审批时必须被拒绝
- `test_invalid_args_rejected_before_run` —— 参数校验在 `run()` 之前，`run` 不被调用
- `test_path_whitelist_blocks_escape` —— 试图写 `../../etc/x` 被拒
- `test_output_truncated_with_hint` —— 超长观察结果被截断且附"剩余 N 字符"
- `test_discovery_two_level_routing` —— 工具 > 阈值时先查索引再加载完整定义
- `test_mcp_tool_implements_same_protocol` —— MCP 工具与内置工具同一接口，无第二条执行路径

**从 M1 移入（M1 只能测纯函数，系统级验证在这里）**：

- `test_tool_runs_inside_sandbox_root` —— 真实执行时工作目录被限制在 sandbox root，写 `../` 被拒
- `test_dangerous_action_requires_approval` —— 执行层拦截，策略由 M1 的 `test_policy_requires_approval_for_exec` 验证
- `test_feedback_signal_is_structured` —— 工具错误结构化喂回（文件路径+行号+错误类型+建议）
- `test_rollback_available_before_destructive_action` —— 破坏性动作前必须有快照/分支可用，否则拒绝执行
- `test_parallel_batch_failure_isolation` —— 一个工具失败只在同批并行调用内传播，不取消独立调用

## 三、人工判断

1. **工具描述能不能让模型"一次看懂该传什么参数"？** 这是 ch4 的 ACI 原则，程序验不了，只能人读。看不懂就改描述。
2. **感知类工具的输出是否分页/截断，而不是一次倒出 2000 行？** 感知工具控信息量。
3. **危险操作的审批提示是否写清"要改哪个文件、影响什么"？** 只写"是否允许"等于没写。

## Skill 留痕（必填，空着即未完成）

- 加载：`tool-design`、`tool-discovery`、`mcp-skill-hub`
- 采纳结论：（逐条：五类工具划分 / ACI 原则 / 两级路由阈值 / MCP 安全清单）
- 冲突与裁决：
- 实测差异：

## 四、拒绝条件

- 感知工具与执行工具共用同一套截断与审批策略（二者的设计约束不同）。
- MCP 工具走独立执行路径（后期"按需发现"与"审批"会失效）。
- 工具集变化导致静态前缀哈希变化却没有记录。
- 危险工具仅靠提示词约束，没有执行层拦截。
