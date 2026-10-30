"""旧 agentbook-rag 包的路径桥接。

见 docs/adr/0001：本仓库不复制、不修改旧包，只通过 AIDE_RAG_HOME 注入 sys.path。
所有 sys.path 操作集中在本文件，业务代码不得再出现同类写法。
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

DEFAULT_RAG_HOME = Path.home() / "Documents" / "ChatGPT" / "个人网站"

# 默认离线：模型已缓存时不该联网。
# 否则守卫会因为网络失败而崩，与"向量错位"走同一条失败路径，退出码无法区分。
# 需要首次下载模型或排查网络时设 AIDE_ALLOW_HF_NETWORK=1。
# 必须在任何 transformers/huggingface_hub 导入之前生效，故放在模块顶层。
if not os.environ.get("AIDE_ALLOW_HF_NETWORK"):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class RagHomeMissing(RuntimeError):
    """AIDE_RAG_HOME 不存在或缺少年 agentbook-rag 目录。"""


def rag_package_root() -> Path:
    home = Path(os.environ.get("AIDE_RAG_HOME") or DEFAULT_RAG_HOME).expanduser()
    if not home.is_dir():
        raise RagHomeMissing(
            f"AIDE_RAG_HOME 不存在：{home}\n"
            "请设置环境变量 AIDE_RAG_HOME 指向包含 agentbook-rag/ 的仓库根目录。"
        )
    package = home / "agentbook-rag"
    if not package.is_dir():
        raise RagHomeMissing(f"AIDE_RAG_HOME 下缺少 agentbook-rag/：{package}")
    return package


def _ensure_on_path() -> Path:
    root = rag_package_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


@lru_cache(maxsize=1)
def load_core() -> SimpleNamespace:
    """不依赖 torch 的部分：分块、索引、BM25、RRF。"""
    _ensure_on_path()
    from agentbook_rag.bm25 import BM25Index, tokenize
    from agentbook_rag.chunking import Chunk, chunk_markdown
    from agentbook_rag.index import NumpyIndex, RetrievalResult
    from agentbook_rag.rrf import reciprocal_rank_fusion

    return SimpleNamespace(
        Chunk=Chunk,
        chunk_markdown=chunk_markdown,
        NumpyIndex=NumpyIndex,
        RetrievalResult=RetrievalResult,
        BM25Index=BM25Index,
        tokenize=tokenize,
        rrf=reciprocal_rank_fusion,
    )


def _install_last_token_pooling_fix() -> None:
    """修正旧包在 left padding 下的 last-token pooling 取错位置。

    旧实现用 `attention_mask.sum(dim=1) - 1` 作为最后一个 token 的下标，
    这只在 **right padding** 下成立。QwenEmbedder 构造 tokenizer 时明确用
    `padding_side="left"`，此时真实 token 位于序列末尾，正确取法是最后一位。

    后果：同一 batch 内长度不足 512 token 的文本（文件尾块、短文档）会取到
    padding 位置的隐状态，向量与文本不再对应。v1 索引因为绝大多数 chunk 都是
    满 512 token 而侥幸未暴露。

    见 docs/adr/0002-embedding-pooling-fix.md。此处只在运行时覆盖模块级函数，
    不修改上游文件。
    """

    import agentbook_rag.embedding as legacy

    def _left_padded_last_token_pool(last_hidden_state, attention_mask):
        # 把"末位一定是真实 token"这个假设变成契约：上游若改回 right padding，
        # 这里必须立刻炸掉，而不是再次静默产出错位向量。
        if int(attention_mask[:, -1].sum()) != attention_mask.shape[0]:
            raise ValueError(
                "last-token pooling 假设 left padding，但 batch 末列存在 pad 位置；"
                "上游 embedding 可能已改回 right padding，见 docs/adr/0002"
            )
        return last_hidden_state[:, -1]

    legacy._last_token_pool = _left_padded_last_token_pool


@lru_cache(maxsize=1)
def load_embedding() -> SimpleNamespace:
    """Dense 向量化，依赖 torch/transformers。"""
    _ensure_on_path()
    from agentbook_rag.embedding import DEFAULT_MODEL, QwenEmbedder

    _install_last_token_pooling_fix()
    return SimpleNamespace(DEFAULT_MODEL=DEFAULT_MODEL, QwenEmbedder=QwenEmbedder)


@lru_cache(maxsize=1)
def load_reranker() -> SimpleNamespace:
    """Cross-Encoder 重排，依赖 torch/transformers。"""
    _ensure_on_path()
    from agentbook_rag.reranker import DEFAULT_RERANKER_MODEL, CrossEncoderReranker

    return SimpleNamespace(
        DEFAULT_MODEL=DEFAULT_RERANKER_MODEL,
        CrossEncoderReranker=CrossEncoderReranker,
    )


def tokenizer(model_name: str | None = None):
    """构建分块用的 tokenizer（需要 transformers）。"""
    _ensure_on_path()
    from transformers import AutoTokenizer

    from agentbook_rag.embedding import DEFAULT_MODEL

    return AutoTokenizer.from_pretrained(model_name or DEFAULT_MODEL)
