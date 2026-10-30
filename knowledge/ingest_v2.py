"""构建 corpus-v2 索引：书稿全书 12 文件 + 个人资料。

分块、嵌入、向量索引、BM25 索引全部复用旧 agentbook-rag 的实现（见 docs/adr/0001），
本脚本只负责：读语料清单 → 逐文件分块 → 批量嵌入 → 落盘 → 记录 manifest（含 sha256）。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

import loader

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = Path(__file__).with_name("corpus.yaml")

# 退出码：把"环境不可用"和"数据错位"分开，CI 与人工排查一眼可辨。
#
# 2 号与 argparse 撞车：参数写错时 argparse 自己也返回 2。因此把 2 定义成
# 「调用方错误」——包括命令行参数非法与上游路径/语料清单配置错误，两者都要求
# 调用方改命令或改配置，而不是排查运行环境。若需要严格区分，用 4 判定环境问题。
EXIT_OK = 0
EXIT_USAGE = 2  # 调用方错误：参数非法 / 上游路径缺失 / 语料清单与实际不符
EXIT_INDEX_MISMATCH = 3  # 向量与文本错位（数据问题）
EXIT_UNAVAILABLE = 4  # 模型或索引加载失败（环境/网络问题）
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CorpusError(RuntimeError):
    """语料定义或来源缺失。"""


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_root(root: Path, label: str) -> None:
    if not root.is_dir():
        raise CorpusError(f"语料来源 {label} 不存在：{root}（请检查 .env 中的路径变量）")


def resolve_files(spec: dict[str, Any]) -> list[tuple[str, Path]]:
    """返回 (相对语料根的路径, 绝对路径) 列表。"""
    root = Path(_expand(spec["root"])).expanduser()
    _check_root(root, spec.get("prefix", "?"))

    found: list[Path] = []
    if spec.get("files"):
        for name in _expand(spec["files"]):
            path = root / name
            if not path.is_file():
                raise CorpusError(f"声明了但文件不存在：{path}")
            found.append(path)
    for pattern in _expand(spec.get("globs") or []):
        matches = sorted(Path(p) for p in glob.glob(str(root / pattern)))
        matches = [p for p in matches if p.is_file()]
        if not matches:
            print(f"[warn] glob 未匹配到任何文件：{root / pattern}", file=sys.stderr)
        found.extend(matches)

    excluded = set(_expand(spec.get("excluded") or []))
    unique: dict[str, Path] = {}
    for path in found:
        rel = path.relative_to(root).as_posix()
        if rel in excluded or path.name in excluded:
            continue
        unique.setdefault(rel, path)
    return sorted(unique.items())


def build_chunks(corpus: dict[str, Any], model_name: str) -> tuple[list[Any], dict[str, Any]]:
    core = loader.load_core()
    tokenizer = loader.tokenizer(model_name)
    chunk_size = int(corpus.get("chunk_size_tokens", 512))
    overlap = int(corpus.get("overlap_tokens", 64))

    chunks: list[Any] = []
    inventory: dict[str, Any] = {}
    for label, spec in corpus["sources"].items():
        prefix = spec.get("prefix", label)
        files = resolve_files(spec)
        per_source: list[dict[str, Any]] = []
        for rel, absolute in files:
            text = absolute.read_text(encoding="utf-8")
            produced = core.chunk_markdown(
                text,
                tokenizer,
                source_repo=spec.get("source_repo", label),
                commit=spec.get("commit", "unknown"),
                file=f"{prefix}/{rel}",
                chunk_size=chunk_size,
                overlap=overlap,
            )
            chunks.extend(produced)
            per_source.append(
                {
                    "file": f"{prefix}/{rel}",
                    "sha256": _sha256(absolute),
                    "chunks": len(produced),
                    "chars": len(text),
                }
            )
        inventory[label] = {"prefix": prefix, "root": str(Path(_expand(spec["root"]))), "files": per_source}
        total = sum(item["chunks"] for item in per_source)
        print(f"[chunk] {label}: {len(per_source)} 文件 -> {total} chunks")
    return chunks, inventory


SELF_SIMILARITY_FLOOR = 0.999


class EmbeddingUnavailable(RuntimeError):
    """嵌入模型或索引无法加载——属于环境问题，必须与"数据错位"区分开。"""


def verify_embeddings(dense_dir: Path, *, model_name: str, sample: int = 10) -> dict[str, Any]:
    """构建期守卫：抽样重编码，自相似度必须高于下限。

    这是「静默失效」唯一可靠的拦截点——模型此时已在内存/磁盘缓存里。
    用 batch_size=1 重编码：单条前向不产生 padding，因此无论上游用 left 还是
    right padding 的池化实现，结果都一致——**它测的是索引本身，而不是修复是否生效**。
    任何向量与文本的错位都会立刻表现为自相似度远低于 1。

    加载失败（模型缺失、网络、路径错）抛 EmbeddingUnavailable，由调用方映射成
    独立的退出码，避免和"数据错位"共用一条失败路径。
    """
    core = loader.load_core()
    try:
        index = core.NumpyIndex.load(dense_dir)
    except (OSError, ValueError) as error:
        raise EmbeddingUnavailable(f"索引无法加载：{dense_dir}（{error}）") from error
    total = len(index.chunks)
    # 抽样：首/中/尾 + 最短的若干（最易触发 padding 错位）+ 最长的
    by_length = sorted(range(total), key=lambda i: len(index.chunks[i].text))
    picks = {0, total // 2, total - 1}
    picks.update(by_length[: max(2, sample // 2)])
    picks.update(by_length[-2:])

    try:
        embedder = loader.load_embedding().QwenEmbedder(model_name=model_name, batch_size=1)
    except Exception as error:  # noqa: BLE001 —— 需要把"加载失败"整体归类，不细分异常类型
        raise EmbeddingUnavailable(f"嵌入模型无法加载：{model_name}（{error}）") from error

    worst = 1.0
    failures: list[dict[str, Any]] = []
    for index_in_list in sorted(picks):
        chunk = index.chunks[index_in_list]
        try:
            fresh = embedder.encode_documents([chunk.embedding_text]).numpy()[0]
        except Exception as error:  # noqa: BLE001
            raise EmbeddingUnavailable(f"嵌入推理失败：{error}") from error
        similarity = float(fresh @ index.embeddings[index_in_list])
        worst = min(worst, similarity)
        if similarity <= SELF_SIMILARITY_FLOOR:
            failures.append(
                {
                    "chunk": f"{chunk.file}#{chunk.chunk_index}",
                    "self_similarity": round(similarity, 4),
                    "text_chars": len(chunk.text),
                }
            )
    return {"checked": len(picks), "worst": round(worst, 4), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 corpus-v2 索引")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "artifacts/corpus-v2")
    parser.add_argument("--model", default=os.environ.get("AIDE_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"))
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--skip-dense", action="store_true", help="只建 BM25（用于快速自检）")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="不构建，只对已有 Dense 索引跑自相似度守卫",
    )
    parser.add_argument("--dense-dir", type=Path, default=None, help="配合 --verify-only 直接指定索引目录")
    args = parser.parse_args()

    if args.verify_only:
        target = args.dense_dir or (args.output_dir / "dense")
        try:
            report = verify_embeddings(target, model_name=args.model)
        except EmbeddingUnavailable as error:
            print(f"[verify][UNAVAILABLE] {error}", file=sys.stderr)
            print(
                "提示：默认离线运行；首次下载模型请设 AIDE_ALLOW_HF_NETWORK=1",
                file=sys.stderr,
            )
            return EXIT_UNAVAILABLE
        print(f"[verify] 抽样 {report['checked']} 条，最差自相似度 {report['worst']:.4f}")
        for failure in report["failures"]:
            print(f"[verify][FAIL] {failure}", file=sys.stderr)
        return EXIT_OK if not report["failures"] else EXIT_INDEX_MISMATCH

    started = time.time()
    corpus = _expand(yaml.safe_load(args.corpus.read_text(encoding="utf-8")))
    chunks, inventory = build_chunks(corpus, args.model)
    if not chunks:
        raise CorpusError("没有生成任何 chunk，检查 corpus.yaml")
    print(f"[chunk] 合计 {len(chunks)} chunks")

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    core = loader.load_core()

    bm25 = core.BM25Index.from_chunks(
        chunks,
        manifest={"corpus": "corpus-v2", "source_files": sum(len(v["files"]) for v in inventory.values())},
    )
    bm25.save(out / "bm25")
    print(f"[bm25] {len(chunks)} chunks -> {out / 'bm25'}")

    if not args.skip_dense:
        try:
            embedding = loader.load_embedding()
            embedder = embedding.QwenEmbedder(model_name=args.model, batch_size=args.batch_size)
            texts = [chunk.embedding_text for chunk in chunks]
            vectors = embedder.encode_documents(texts).numpy()
        except Exception as error:  # noqa: BLE001
            print(
                f"[error] 嵌入不可用：{error}\n"
                "提示：默认离线运行；首次下载模型请设 AIDE_ALLOW_HF_NETWORK=1",
                file=sys.stderr,
            )
            return EXIT_UNAVAILABLE
        dense = core.NumpyIndex(
            chunks,
            vectors,
            {
                "corpus": "corpus-v2",
                "model": args.model,
                "device": str(embedder.device),
                "chunk_size_tokens": int(corpus.get("chunk_size_tokens", 512)),
                "overlap_tokens": int(corpus.get("overlap_tokens", 64)),
                "metric": "cosine",
                "embedding_text": "static source/commit/file/heading prefix plus raw chunk",
            },
        )
        dense.save(out / "dense")
        print(f"[dense] {len(chunks)} chunks x {vectors.shape[1]} dims -> {out / 'dense'}")

        # 在环内阻断：守卫失败则本次构建直接非零退出，不允许产出"看起来正常"的坏索引。
        try:
            report = verify_embeddings(out / "dense", model_name=args.model)
        except EmbeddingUnavailable as error:
            print(f"[verify][UNAVAILABLE] {error}", file=sys.stderr)
            print("提示：默认离线运行；首次下载模型请设 AIDE_ALLOW_HF_NETWORK=1", file=sys.stderr)
            return EXIT_UNAVAILABLE
        print(f"[verify] 抽样 {report['checked']} 条，最差自相似度 {report['worst']:.4f}")
        if report["failures"]:
            for failure in report["failures"]:
                print(f"[verify][FAIL] {failure}", file=sys.stderr)
            print(
                "[abort] 向量与文本错位，索引不可用；排查见 docs/adr/0002（退出码 3）",
                file=sys.stderr,
            )
            return EXIT_INDEX_MISMATCH

    manifest = {
        "corpus": "corpus-v2",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": round(time.time() - started, 1),
        "chunk_count": len(chunks),
        "chunk_size_tokens": int(corpus.get("chunk_size_tokens", 512)),
        "overlap_tokens": int(corpus.get("overlap_tokens", 64)),
        "embedding_model": args.model,
        "dense_skipped": bool(args.skip_dense),
        "sources": inventory,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[done] {len(chunks)} chunks in {manifest['elapsed_seconds']}s -> {out}")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EmbeddingUnavailable as error:
        print(f"[error] 嵌入不可用：{error}", file=sys.stderr)
        print("提示：默认离线运行；首次下载模型请设 AIDE_ALLOW_HF_NETWORK=1", file=sys.stderr)
        raise SystemExit(EXIT_UNAVAILABLE)
    except (CorpusError, loader.RagHomeMissing) as error:
        print(f"[error] {error}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
