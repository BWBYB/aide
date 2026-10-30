# AIDE 统一入口。所有命令都必须在仓库根目录执行。
SHELL := /bin/bash
.DEFAULT_GOAL := help

AIDE_PY ?= python3
RAG_HOME ?= $(HOME)/Documents/ChatGPT/个人网站
BOOK_HOME ?= $(HOME)/Documents/ai-agent-book
PERSONAL_HOME ?= $(HOME)/Documents/ChatGPT/个人网站
INDEX_DIR ?= artifacts/corpus-v2
export AIDE_RAG_HOME := $(RAG_HOME)
export BOOK_HOME := $(BOOK_HOME)
export PERSONAL_HOME := $(PERSONAL_HOME)
export PYTHONPATH := knowledge

.PHONY: help
help:
	@echo "make check-env   检查上游仓库与 Python 依赖"
	@echo "make corpus      构建 corpus-v2 索引（Dense + BM25）"
	@echo "make eval-v1     在 corpus-v2 上重跑旧冻结题集（基线漂移）"
	@echo "make test        单元测试"
	@echo "make status      打印里程碑验收状态"

.PHONY: check-env
check-env:
	@echo "python      = $(AIDE_PY)"
	@$(AIDE_PY) -c "import sys;print('python ver  =', sys.version.split()[0])"
	@$(AIDE_PY) -c "import torch;print('torch       =', torch.__version__, '| mps =', torch.backends.mps.is_available())"
	@$(AIDE_PY) -c "import transformers, rank_bm25, ranx, yaml;print('deps        = ok')"
	@test -d "$(BOOK_HOME)/book" && echo "book_home   = $(BOOK_HOME)/book"
	@test -d "$(RAG_HOME)/agentbook-rag" && echo "rag_home    = $(RAG_HOME)/agentbook-rag"
	@test -d "$(PERSONAL_HOME)/knowledge" && echo "personal    = $(PERSONAL_HOME)/knowledge"

.PHONY: corpus
corpus:
	$(AIDE_PY) knowledge/ingest_v2.py --output-dir $(INDEX_DIR)

.PHONY: eval-v1
eval-v1:
	$(AIDE_PY) knowledge/eval_v2.py --index-dir $(INDEX_DIR) --out docs/eval/retrieval-v2.md

# M0 新增题集（ch4–10 + 个人资料）：无跨语料基线，只做 E0–E4 横向比较
.PHONY: eval-m0
eval-m0:
	$(AIDE_PY) knowledge/eval_v2.py \
		--index-dir $(INDEX_DIR) \
		--questions knowledge/questions/m0.v2.jsonl \
		--rewrites artifacts/eval-v2-m0/rewrites.jsonl \
		--out-dir artifacts/eval-v2-m0 \
		--out docs/eval/retrieval-m0-15q.md \
		--baseline none

# 合并 36 题（21 冻结 + 15 新增），M0 的真正判据
.PHONY: eval-36
eval-36:
	$(AIDE_PY) knowledge/merge_sets.py knowledge/questions/all36.v2.jsonl \
		"$(RAG_HOME)/agentbook-rag/evaluation/frozen/questions.v1.test.jsonl" \
		knowledge/questions/m0.v2.jsonl
	$(AIDE_PY) knowledge/merge_sets.py artifacts/eval-v2-all36/rewrites.jsonl \
		"$(RAG_HOME)/agentbook-rag/artifacts/evaluation/questions.v1.test.rewrites.jsonl" \
		artifacts/eval-v2-m0/rewrites.jsonl
	$(AIDE_PY) knowledge/eval_v2.py \
		--questions knowledge/questions/all36.v2.jsonl \
		--rewrites artifacts/eval-v2-all36/rewrites.jsonl \
		--out-dir artifacts/eval-v2-all36 \
		--out docs/eval/retrieval-36q.md \
		--baseline none

# 单变量实验：候选池 20 -> 50
.PHONY: eval-36-k50
eval-36-k50:
	$(AIDE_PY) knowledge/eval_v2.py \
		--questions knowledge/questions/all36.v2.jsonl \
		--rewrites artifacts/eval-v2-all36/rewrites.jsonl \
		--out-dir artifacts/eval-v2-all36-k50 \
		--out docs/eval/retrieval-36q-k50.md \
		--baseline none --candidate-k 50

# 向量自相似度守卫（构建时已强制执行，这里用于事后复核）
.PHONY: verify-index
verify-index:
	$(AIDE_PY) knowledge/ingest_v2.py --verify-only

.PHONY: test
test:
	$(AIDE_PY) -m pytest

.PHONY: status
status:
	@cat docs/status.md

# 改完验收清单先跑这个：对比上一版断言集合，一行看出有没有丢
# 注意 comm 要求输入已排序，忘记 sort -u 会静默给出错误结果。
SPEC ?= docs/acceptance/M1-harness-core.md
.PHONY: check-spec-diff
check-spec-diff:
	@git show HEAD~1:$(SPEC) 2>/dev/null | grep -oE "test_[a-z_]+" | sort -u > /tmp/spec-old.txt || true
	@grep -oE "test_[a-z_]+" $(SPEC) | sort -u > /tmp/spec-new.txt
	@echo "旧有今无（疑似丢失）："; comm -23 /tmp/spec-old.txt /tmp/spec-new.txt
	@echo "今有旧无（新增或改名）："; comm -13 /tmp/spec-old.txt /tmp/spec-new.txt
	@echo "当前条数：$$(wc -l < /tmp/spec-new.txt | tr -d ' ')"
