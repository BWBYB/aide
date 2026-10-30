# ADR 0001：知识层采用适配器桥接

- 状态：已接受
- 日期：2026-10-30
- 关联里程碑：M0

## 背景

旧 RAG 实现位于 `~/Documents/ChatGPT/个人网站/agentbook-rag`，是一个普通 Python 包（只有 `requirements.txt`，没有 `pyproject.toml`）。用户明确要求"暂时不动，需要时再讨论"。

## 决策

新仓库不复制、不修改旧包。`knowledge/loader.py` 通过 `AIDE_RAG_HOME` 环境变量定位旧仓库并注入 `sys.path`，只取四个符号：`chunk_markdown`、`QwenEmbedder`、`NumpyIndex`、`BM25Index`。

## 被否决的方案

1. **复制旧包进新仓库**：会产生两份真相，旧包修复不会自动生效，评估基线无法对齐。
2. **声明为 PEP 508 路径依赖**：旧包无构建后端，`pip install -e` 会失败。
3. **重写检索层**：浪费已验证的 RRF + 重排实现（旧项目 E3/E4 对照已有结论）。

## 后果

- 正面：旧包 37 个测试与既有基线零影响；迁移时机可独立决定。
- 负面：多一层间接，`sys.path` 注入属于隐式依赖，必须集中在一处。
- 退出条件：若旧包被加上 `pyproject.toml`，本 ADR 作废，改为正常依赖。
