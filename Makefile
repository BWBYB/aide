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

# 出处覆盖：每条断言必须标 skill:行号 或「自定」
# 与 check-spec-diff 成对：一个查「有没有少」，一个查「有没有编」
.PHONY: check-provenance
check-provenance:
	@awk '/^## 三、自动检查/,/^## 三之二/' $(SPEC) | grep -E '^\| `test_' > /tmp/spec-rows.txt
	@echo "断言行数：$$(wc -l < /tmp/spec-rows.txt | tr -d ' ')"
	@echo "有行号：$$(grep -cE '[A-Za-z0-9._-]+:[0-9]' /tmp/spec-rows.txt | tr -d ' ')"
	@echo "标自定：$$(grep -c '自定' /tmp/spec-rows.txt | tr -d ' ')"
	@grep -vE '[A-Za-z0-9._-]+:[0-9]|自定' /tmp/spec-rows.txt > /tmp/spec-bad.txt || true
	@test ! -s /tmp/spec-bad.txt || { echo "[error] 以下断言既无行号也未标自定："; cat /tmp/spec-bad.txt; exit 1; }
	@echo "出处覆盖：完整"

# 入口：M1a 用替身跑通，M1b 才换真实 provider（见 ADR 0008）
CLI_ENV = PYTHONPATH=src:knowledge
.PHONY: run
run:
	@test -n "$(TASK)" || { echo "[error] 用法：make run TASK=\"...\""; exit 2; }
	@$(CLI_ENV) $(AIDE_PY) -m aide.cli run --task "$(TASK)" --trace "$(or $(TRACE),traces/latest.jsonl)"

.PHONY: replay
replay:
	@test -n "$(TRACE)" || { echo "[error] 用法：make replay TRACE=traces/xxx.jsonl"; exit 2; }
	@$(CLI_ENV) $(AIDE_PY) -m aide.cli replay --trace "$(TRACE)"

.PHONY: test
test:
	$(AIDE_PY) -m pytest

# 日常回归：纯函数与替身，无网络、无模型
.PHONY: test-unit
test-unit:
	$(AIDE_PY) -m pytest -m "not contract and not slow"

# 契约测试：验证「对方怎么收」，需要真实 key 与网络
.PHONY: test-contract
test-contract:
	@test -n "$$AIDE_CONTRACT" || { echo "[skip] 设 AIDE_CONTRACT=1 并配置 key 后再跑"; exit 1; }
	$(AIDE_PY) -m pytest -m contract

.PHONY: status
status:
	@cat docs/status.md

# 加载 skill 并留痕（带 sha256）：make skill-load SKILLS="a b" MILESTONE=M1
# 手工写「已加载」不算加载——判定标准是日志里有没有这一行。
.PHONY: skill-load
skill-load:
	@test -n "$(SKILLS)" || { echo "[error] 用法：make skill-load SKILLS=\"a b\" MILESTONE=M1"; exit 2; }
	python3 tools/load_skill.py $(SKILLS) --for $(or $(MILESTONE),-)

# 改完验收清单先跑这个：对比基线断言集合，一行看出有没有丢
# - BASE 默认 HEAD：问的是"这次改动有没有丢条目"；查历史用 BASE=HEAD~3
# - comm 要求输入已排序，漏 sort -u 会静默给出错误结果
# - 基线为空必须报错：否则 SPEC/BASE 写错时输出仍是"旧有今无：空"，看起来一切正常
SPEC ?= docs/acceptance/M1-harness-core.md
BASE ?= HEAD
.PHONY: check-spec-diff
check-spec-diff:
	@git show $(BASE):$(SPEC) 2>/dev/null | grep -oE "test_[a-z0-9_]+" | sort -u > /tmp/spec-old.txt || true
	@test -s /tmp/spec-old.txt || { echo "[error] 基线为空，检查 SPEC=$(SPEC) BASE=$(BASE)"; exit 1; }
	@grep -oE "test_[a-z0-9_]+" $(SPEC) | sort -u > /tmp/spec-new.txt
	@echo "旧有今无（疑似丢失）："; comm -23 /tmp/spec-old.txt /tmp/spec-new.txt
	@echo "今有旧无（新增或改名）："; comm -13 /tmp/spec-old.txt /tmp/spec-new.txt
	@echo "断言条数（仅自动检查节内表格行）：$$(awk '/^## 三、自动检查/,/^## 三之二/' $(SPEC) | grep -cE '^\| `test_' | tr -d ' ')"
	@echo "全文去重条数（含正文引用，仅供比对）：$$(wc -l < /tmp/spec-new.txt | tr -d ' ')"
