"""在 corpus-v2 索引里按关键词定位 chunk，供人工标注 gold_chunk_ids 使用。

用法：
    python knowledge/lookup.py "Pass@k Pass^k" --limit 6
    python knowledge/lookup.py "失败模式" --prefix book/chapter10.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "artifacts/corpus-v2"


def load_chunks(index_dir: Path) -> list[dict]:
    for sub in ("dense", "bm25"):
        path = index_dir / sub / "chunks.jsonl"
        if path.is_file():
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    raise FileNotFoundError(f"{index_dir} 下找不到 dense/bm25 的 chunks.jsonl")


def snippet(text: str, terms: list[str], width: int = 90) -> str:
    lowered = text.lower()
    for term in terms:
        hit = lowered.find(term.lower())
        if hit >= 0:
            start = max(0, hit - width // 2)
            piece = text[start : start + width].replace("\n", " ")
            return ("…" if start else "") + piece + "…"
    return text[:width].replace("\n", " ") + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="按关键词定位 chunk")
    parser.add_argument("query", help="关键词，空格分隔，任一命中即算")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--prefix", default=None, help="只搜索该文件前缀，如 book/chapter4.md")
    args = parser.parse_args()

    terms = [term for term in re.split(r"\s+", args.query.strip()) if term]
    chunks = load_chunks(args.index_dir)
    if args.prefix:
        chunks = [chunk for chunk in chunks if chunk["file"].startswith(args.prefix)]

    scored = []
    for chunk in chunks:
        lowered = chunk["text"].lower()
        hits = sum(1 for term in terms if term.lower() in lowered)
        if hits:
            scored.append((hits, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]["file"], item[1]["chunk_index"]))

    if not scored:
        print("[none] 没有命中")
        return 1
    for hits, chunk in scored[: args.limit]:
        heading = " > ".join(chunk["heading_path"]) or "(root)"
        print(f"{chunk['file']}#{chunk['chunk_index']}  hits={hits}  [{heading}]")
        print(f"    {snippet(chunk['text'], terms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
