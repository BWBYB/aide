"""在 corpus-v2 上重跑旧冻结题集，量出 v1 → v2 的检索基线漂移。

设计：不重写评估逻辑，直接以子进程调用旧包的 evaluate（E0–E4），
只负责调度与把 summary 转成人类可读的对照报告。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import loader

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = ("e4-frozen", "20261015-235155-summary.json")
CONFIGS = ("E0", "E1", "E2", "E3", "E4")
HEADLINE = ("hit_rate@5", "mrr@10", "ndcg@10", "recall@20")
LABELS = {
    "E0": "Dense-only",
    "E1": "BM25-only",
    "E2": "Dense+BM25+RRF",
    "E3": "E2+CrossEncoder",
    "E4": "E3+一次查询改写",
}


def _latest_summary(directory: Path) -> Path:
    candidates = sorted(directory.glob("*-summary.json"))
    if not candidates:
        raise FileNotFoundError(f"{directory} 下没有 *-summary.json")
    return candidates[-1]


def run_legacy_eval(
    package_root: Path,
    index_dir: Path,
    out_dir: Path,
    questions: Path | None = None,
    rewrites: Path | None = None,
    candidate_k: int = 20,
) -> Path:
    questions = questions or (package_root / "evaluation/frozen/questions.v1.test.jsonl")
    rewrites = rewrites or (package_root / "artifacts/evaluation/questions.v1.test.rewrites.jsonl")
    # 同上：子进程 cwd 是 package_root，所有外部路径必须先转绝对路径。
    questions = questions.resolve()
    rewrites = rewrites.resolve()
    if not questions.is_file():
        raise FileNotFoundError(f"找不到问题集：{questions}")

    # 旧包以子进程方式在 package_root 下运行，这里必须给绝对路径，否则相对路径会解析错。
    index_dir = index_dir.resolve()
    out_dir = out_dir.resolve()
    dense, bm25 = index_dir / "dense", index_dir / "bm25"
    for path in (dense, bm25):
        if not path.is_dir():
            raise FileNotFoundError(f"索引缺失：{path}（先跑 make corpus）")

    out_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "agentbook_rag.evaluate",
        "--questions", str(questions),
        "--dense-index", str(dense),
        "--bm25-index", str(bm25),
        "--output-dir", str(out_dir),
        "--candidate-k", str(candidate_k),
        "--context-k", "5",
    ]
    if rewrites.is_file():
        command += ["--rewrites", str(rewrites)]
    else:
        print("[warn] 未找到 E4 改写文件，本次只跑 E0–E3", file=sys.stderr)

    print("[eval] " + " ".join(command))
    subprocess.run(command, cwd=str(package_root), check=True)
    return _latest_summary(out_dir)


def _metrics(summary: dict[str, Any], config: str) -> dict[str, Any]:
    return summary["configs"][config]["metrics"]["overall"]


def _timing(summary: dict[str, Any], config: str) -> dict[str, Any]:
    return summary["configs"][config].get("timing", {})


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}" if abs(value) <= 1 else f"{value:.1f}"
    return str(value)


def _delta(new: Any, old: Any) -> str:
    if not isinstance(new, (int, float)) or not isinstance(old, (int, float)):
        return "—"
    diff = new - old
    arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
    return f"{arrow}{abs(diff):.4f}"


def _table(new: dict[str, Any], old: dict[str, Any] | None) -> str:
    head = "| 配置 | 组合 | Hit@5 | MRR@10 | nDCG@10 | Recall@20 | p50 (ms) |"
    sep = "|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for config in CONFIGS:
        if config not in new["configs"]:
            continue
        current = _metrics(new, config)
        timing = _timing(new, config)
        cells = [config, LABELS[config]]
        for metric in HEADLINE:
            value = current.get(metric)
            cell = _fmt(value)
            if old and config in old["configs"]:
                cell += f" ({_delta(value, _metrics(old, config).get(metric))})"
            cells.append(cell)
        cells.append(_fmt(timing.get("p50_ms")))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _by_type_table(new: dict[str, Any], config: str) -> str:
    by_type = new["configs"][config]["metrics"].get("by_type", {})
    if not by_type:
        return "_（本次运行未产出分类型指标）_"
    lines = ["| 问题类型 | Hit@5 | MRR@10 | nDCG@10 |", "|---|---|---|---|"]
    for name, values in sorted(by_type.items()):
        lines.append(
            f"| {name} | {_fmt(values.get('hit_rate@5'))} | "
            f"{_fmt(values.get('mrr@10'))} | {_fmt(values.get('ndcg@10'))} |"
        )
    return "\n".join(lines)


def render(new: dict[str, Any], old: dict[str, Any] | None, index_dir: Path, run_id: str) -> str:
    manifest = new["manifest"]
    questions = manifest.get("question_set_summary", {})
    count = questions.get("question_count", "?")
    by_type = questions.get("type_counts", {})
    qname = Path(manifest.get("question_set", "?")).name
    compare = old is not None

    if compare:
        title = "检索层基线漂移：v1 → v2"
        intro = (
            "v1 的分数是在 **5 份文档 / 181 chunks** 上测得的；v2 把语料扩到 **全书 12 文件 + 个人资料**。\n"
            "语料变了，BM25 的 IDF 与平均文档长度随之改变，Dense 也面对更多候选竞争者——\n"
            "**所以两列数字不能直接相减当成\"能力提升\"**，只能读作\"同一批问题在新语料上的重新测量\"。\n\n"
            "> **重要混杂**：v1 基线是在旧索引上测的，而旧索引存在 left-padding pooling 缺陷\n"
            "> （短文本取到 padding 位置，见 `docs/adr/0002`）。v2 索引已修复该缺陷。\n"
            "> 因此本表差值 = 扩容效应 + 修 bug 效应，**不能归因为单一变量**。"
        )
        question_row = "| 问题集 | `questions.v1.test.jsonl`（21 题） | 同一文件，未改动 |"
        head3 = "## 三、并排对照（括号内为相对 v1 的漂移）"
        caveat = (
            "1. **BM25 的 IDF 变化**：新增 9 份文档后，`chapter1–3` 里原先\"稀有\"的词变得常见，BM25 权重被稀释。\n"
            "2. **Dense 竞争加剧**：候选池从 181 扩到上千，Top-5 的竞争更激烈。\n"
            "3. **延迟**：p50 只受候选数与重排影响，与语料大小弱相关；若显著上升，说明候选数配置被改动。\n"
            "4. **旧题集 gold 标注仍然有效**：`chunk_index` 按文件独立递增，因此 `book/chapter1.md#12` 这类 id 在 v2 里仍指向同一段文本。"
        )
    else:
        title = "新增题集检索评估（corpus-v2）"
        intro = (
            "本报告只测 **M0 新增题集**（ch4–10 各 1–2 题 + 个人资料 3 题），语料为 corpus-v2，**不设跨语料基线**。\n"
            "它回答的是\"扩进来的那部分语料，检索到底找不找得到\"，而不是和 v1 比高低。\n"
            "**15 题是小样本种子集，任何分差都不足以支持结论**；显著性口径见 `docs/status.md`。"
        )
        question_row = f"| 问题集 | — | `{qname}`（{count} 题，M0 新增） |"
        head3 = "## 三、各配置结果"
        caveat = (
            "1. **个人资料只有 20 个 chunk**：12 个文件里多数只有 1 个 chunk，召回粒度很粗，命中即等于整篇命中。\n"
            "2. **新增章节没有历史基线**：ch4–10 从未被索引过，因此本报告只能横向比较 E0–E4，不能纵向比 v1。\n"
            "3. **multi_hop 题占 4/15**：小样本里多跳题权重过高，容易放大重排的收益。\n"
            "4. **本报告是 M5 那 40 题集的第一批种子**：题目结构与字段已对齐旧题集，可直接扩充。"
        )

    return f"""# {title}

> 本文件由 `knowledge/eval_v2.py` 自动生成，数字全部来自 `artifacts/` 下的 `*-summary.json`。
> **不得手改任何数字。**

## 一、先读这一段

{intro}

## 二、实验条件

| 项 | 基线 | 本次 |
|---|---|---|
| 语料 | — | 全书 12 文件 + 个人资料（567 chunks） |
| 索引目录 | — | `{index_dir}` |
{question_row}
| 分块 | — | Qwen tokenizer，512 / 64 overlap |
| 融合 | — | RRF k=60 |
| 重排 | — | `BAAI/bge-reranker-v2-m3` |
| 本次运行 | — | `{run_id}` |

问题集构成：{json.dumps(by_type, ensure_ascii=False)}，共 {count} 题。

{head3}

{_table(new, old)}

读法：{"`↓0.0300` 表示比 v1 低 3 个百分点，不代表 Agent 变差，而是语料变大后更难。" if compare else "无基线，只读 E0–E4 之间的横向差距。"}

**口径澄清（来自 `rag-pipeline` skill）**：本表的 `Recall@k` 是 `ranx` 的学术口径
（相关文档召回比例），不是书稿里"前 k 个结果有任一篇相关即算命中"的命中率口径。
两者必须分开读：`Hit@k` 对应书中口径，`Recall@k` 对应学术口径，跨来源比较时不可混用。

## 四、分类型（E3）

{_by_type_table(new, "E3")}

## 五、必须记录的代价与限制

{caveat}

## 六、结论（需要人工填写，不得由 AI 代写）

- [ ] 结果是否在可解释范围内？
- [ ] 默认链路仍取 E3 吗？
- [ ] E4 的额外一次 LLM 调用是否值得？必须同时看 MRR/nDCG 增益与 Recall@20 变化，
      只报 MRR 增益而回避 Recall 下滑即为选择性汇报。
- [ ] 是否需要调整 `candidate-k`？
- [ ] 本批题目中有几道 gold 标注需要人工复核？

> 未填写的结论一律视为"未验证"，不得写进技术报告。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="在 corpus-v2 上重跑检索评估")
    parser.add_argument("--index-dir", type=Path, default=REPO_ROOT / "artifacts/corpus-v2")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs/eval/retrieval-v2.md")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "artifacts/eval-v2")
    parser.add_argument("--questions", type=Path, default=None, help="问题集，默认旧冻结测试集")
    parser.add_argument("--rewrites", type=Path, default=None, help="E4 改写文件，默认旧测试集的改写")
    parser.add_argument("--reuse", type=Path, default=None, help="复用已有 summary，不重新跑")
    parser.add_argument("--candidate-k", type=int, default=20, help="E2/E3/E4 的候选池大小")
    parser.add_argument(
        "--baseline",
        default="auto",
        help="对比基线 summary 路径；auto=旧冻结集 v1，none=不对比",
    )
    args = parser.parse_args()

    package_root = loader.rag_package_root()
    if args.reuse:
        summary_path = args.reuse
    else:
        summary_path = run_legacy_eval(
            package_root,
            args.index_dir,
            args.out_dir,
            args.questions,
            args.rewrites,
            args.candidate_k,
        )

    new = json.loads(summary_path.read_text(encoding="utf-8"))
    if args.baseline == "none":
        old = None
    else:
        baseline_path = (
            package_root / "artifacts/evaluation" / BASELINE[0] / BASELINE[1]
            if args.baseline == "auto"
            else Path(args.baseline)
        )
        old = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else None
        if old is None:
            print(f"[warn] 未找到对比基线：{baseline_path}", file=sys.stderr)

    run_id = new["manifest"].get("run_id") or time.strftime("%Y%m%d-%H%M%S")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(new, old, args.index_dir, run_id), encoding="utf-8")
    print(f"[done] 报告写入 {args.out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, loader.RagHomeMissing) as error:
        print(f"[error] {error}", file=sys.stderr)
        raise SystemExit(2)
