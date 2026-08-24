"""共享 fixtures：确定性假向量化器 + 示例向量库。"""

from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.rag.retriever import Retriever
from app.storage.vector_store import Chunk, VectorStore, make_chunk_id


class HashEmbedder:
    """确定性假 embedding：按字符 hash 累加，同类文本命中率高，无需真实模型。"""

    def __init__(self, dim: int = 32):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype="float32")
        for ch in text:
            v[ord(ch) % self.dim] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.array([self._vec(t) for t in texts], dtype="float32")

    def embed_query(self, query: str) -> np.ndarray:
        return self._vec(query)


@pytest.fixture
def embedder():
    return HashEmbedder()


@pytest.fixture
def store(embedder) -> VectorStore:
    s = VectorStore(embedder)
    chunks = [
        Chunk(
            id=make_chunk_id("数据结构.txt", None, 0),
            text="链表是一种线性数据结构，节点包含数据和指向下一个节点的指针",
            source="数据结构.txt",
            chunk_index=0,
            metadata={"source": "数据结构.txt"},
        ),
        Chunk(
            id=make_chunk_id("数据结构.txt", None, 1),
            text="树的遍历方式有前序、中序和后序三种",
            source="数据结构.txt",
            chunk_index=1,
            metadata={"source": "数据结构.txt"},
        ),
        Chunk(
            id=make_chunk_id("公司制度.pdf", 1, 0),
            text="公司的年假制度是每位员工每年五天带薪年假",
            source="公司制度.pdf",
            page=1,
            chunk_index=0,
            metadata={"source": "公司制度.pdf", "page": 1},
        ),
    ]
    s.add(chunks)
    return s


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        api_key="test-key",
        data_dir=str(tmp_path / "data"),
        index_dir=str(tmp_path / "data" / "index"),
        conversations_file=str(tmp_path / "data" / "conversations.json"),
        top_k=5,
        similarity_threshold=0.0,
    )


@pytest.fixture
def retriever(store, settings) -> Retriever:
    return Retriever(store, settings)
