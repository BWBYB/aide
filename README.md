# AIDE

学习与求职双轨的个人 Agent。不是聊天套壳，而是一个能跨会话记住你、能调用工具做成事、每次运行都可追溯、可被量化评估、并能从失败轨迹自我改进的系统。

## 核心公式

来自书稿第 1 章：

> **Agent = LLM + 上下文 + 工具**
> 生产形态：**Agent = Model + Harness**，`Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正`

本仓库的模块划分、里程碑划分与验收清单，全部落在这 6 个格子上，见 [`docs/acceptance/README.md`](docs/acceptance/README.md)。

## 三个里程碑先看这里

1. [`docs/acceptance/README.md`](docs/acceptance/README.md) —— 模块映射总表（每步对应 Agent 的哪个模块）
2. [`docs/acceptance/`](docs/acceptance/) —— 每个里程碑的命令、预期输出、人工判断项与拒绝条件
3. [`docs/status.md`](docs/status.md) —— 当前进度（只有用户勾选过的里程碑才标"完成"）

## 知识层

知识层复用旧 RAG 实现（`AIDE_RAG_HOME`），本仓库不复制、不修改其代码，只通过 `knowledge/loader.py` 桥接：

| 组件 | 实现 | 来源 |
|---|---|---|
| Dense | `Qwen/Qwen3-Embedding-0.6B`，1024 维，NumPy 余弦 | 旧包 `embedding.py` / `index.py` |
| 稀疏 | `rank_bm25`，k1=1.5, b=0.75 | 旧包 `bm25.py` |
| 融合 | RRF k=60 | 旧包 `rrf.py` |
| 重排 | `BAAI/bge-reranker-v2-m3` | 旧包 `reranker.py` |
| 分块 | Qwen tokenizer，512 tokens / 64 overlap | 旧包 `chunking.py` |

索引在本仓库重建为 `corpus-v2`：书稿扩到全书 12 文件，并纳入个人资料。

## 环境

```bash
cp .env.example .env
# 若复用旧项目的 venv（已装好 torch/transformers/rank_bm25/ranx）：
export AIDE_PY="$HOME/Documents/ChatGPT/个人网站/backend/.venv/bin/python"
make check-env
```

## 常用命令

```bash
make corpus        # 构建 corpus-v2 索引（Dense + BM25）
make eval-v1       # 在 corpus-v2 上重跑旧冻结题集，量出基线漂移
make test          # 单元测试
make status        # 打印当前里程碑与验收状态
```

## 文档

- `docs/architecture.md` —— 分层与数据流
- `docs/harness-decisions.md` —— 九条关键取舍的动机、被否决方案与实测依据
- `docs/adr/` —— 逐条架构决策记录
- `docs/eval/` —— 检索层与任务层评估报告
