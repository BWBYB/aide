"""corpus-v2 的四项硬检查（对应 docs/acceptance/M0-corpus-v2.md）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "knowledge/corpus.yaml"
INDEX = REPO_ROOT / "artifacts/corpus-v2"
RAG_HOME = Path.home() / "Documents/ChatGPT/个人网站/agentbook-rag"

EXPECTED_BOOK_FILES = [f"chapter{i}.md" for i in range(1, 11)]


def _corpus() -> dict:
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8"))


def _chunk_files(index_dir: Path) -> list[dict]:
    path = index_dir / "chunks.jsonl"
    if not path.is_file():
        pytest.skip(f"索引未构建：{path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_corpus_manifest_has_all_book_files() -> None:
    book = _corpus()["sources"]["book"]
    listed = set(book["files"])
    assert set(EXPECTED_BOOK_FILES) <= listed, "缺少 chapter4–10 中的文件"
    assert "introduction.md" in listed and "afterword.md" in listed
    assert "reference-answers.md" not in listed
    assert "reference-answers.md" in set(book["excluded"])


def test_personal_chunks_use_personal_prefix() -> None:
    files = {item["file"] for item in _chunk_files(INDEX / "bm25")}
    personal = {name for name in files if name.startswith("personal/")}
    assert personal, "语料里没有任何 personal/ 前缀的 chunk"
    assert all(not name.startswith("book/personal") for name in files)


def test_manifest_records_sha256() -> None:
    manifest_path = INDEX / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip(f"manifest 未生成：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [
        entry
        for source in manifest["sources"].values()
        for entry in source["files"]
    ]
    assert entries, "manifest 没有记录任何文件"
    for entry in entries:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), f"sha256 非法：{entry['file']}"
        assert entry["chunks"] > 0, f"文件未产出 chunk：{entry['file']}"


def test_chunk_ids_stable_for_old_files() -> None:
    """chapter1–3 的 file#index 必须与 v1 完全一致，旧题集标注才可复用。"""
    v1_dir = RAG_HOME / "artifacts/qwen-dense-v1"
    if not v1_dir.is_dir():
        pytest.skip(f"找不到 v1 索引：{v1_dir}")

    def ids(directory: Path) -> set[str]:
        return {
            f"{chunk['file']}#{chunk['chunk_index']}"
            for chunk in _chunk_files(directory)
            if chunk["file"].startswith("book/chapter")
        }

    v1 = {item for item in ids(v1_dir) if item.split("#")[0] in {
        "book/chapter1.md", "book/chapter2.md", "book/chapter3.md"
    }}
    v2 = {item for item in ids(INDEX / "dense") if item.split("#")[0] in {
        "book/chapter1.md", "book/chapter2.md", "book/chapter3.md"
    }}
    assert v1 == v2, f"旧文件 chunk id 发生漂移：仅 v1 有 {len(v1 - v2)} 个，仅 v2 有 {len(v2 - v1)} 个"


def test_expand_keeps_unknown_variable_literal() -> None:
    import ingest_v2

    assert ingest_v2._expand("${AIDE_NOT_SET_XYZ}/a") == "${AIDE_NOT_SET_XYZ}/a"


# 离线时序的断言已迁到 tests/test_offline_order.py：
# 进程内断言测不出真实入口模块的导入顺序，改为子进程 + 常量版。


@pytest.mark.skipif(
    not os.environ.get("AIDE_SLOW_TESTS"),
    reason="需要加载嵌入模型；设 AIDE_SLOW_TESTS=1 启用",
)
def test_embeddings_self_consistent() -> None:
    """索引里的向量必须与它自己的文本对应（见 docs/adr/0002）。"""
    import numpy as np

    import loader

    dense = INDEX / "dense"
    chunks = _chunk_files(dense)
    embeddings = np.load(dense / "embeddings.npy")
    embedder = loader.load_embedding().QwenEmbedder()

    # 固定抽样：文件首块、中间块、末尾块、个人资料块
    picks = [0, 100, 300, len(chunks) // 2, len(chunks) - 20, len(chunks) - 1]
    for index in picks:
        fresh = embedder.encode_documents([chunks[index]["embedding_text"]]).numpy()[0]
        similarity = float(fresh @ embeddings[index])
        assert similarity > 0.999, (
            f"{chunks[index]['file']}#{chunks[index]['chunk_index']} 自相似度仅 {similarity:.3f}，"
            "说明向量与文本错位"
        )
