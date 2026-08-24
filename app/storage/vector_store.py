"""向量存储：numpy 矩阵 + 元数据，支持磁盘持久化与 metadata 过滤。

设计取舍：不引入重型向量数据库，用 numpy 做归一化点积（=余弦）批量检索，
既能在面试里讲清「向量检索原理」，又支持持久化与按字段过滤。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class Chunk:
    """知识库中的一个文本块及其元数据。"""

    id: str
    text: str
    source: str  # 来源文件名
    page: int | None = None  # 页码（PDF），txt 为 None
    chunk_index: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**d)


@dataclass
class SearchResult:
    """一次检索命中的结果：块 + 相似度分数。"""

    chunk: Chunk
    score: float


def make_chunk_id(source: str, page: int | None, index: int) -> str:
    """确定性 chunk id，保证持久化后重建时 id 稳定。"""
    raw = f"{source}#{page}#{index}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:16]


class VectorStore:
    """基于 numpy 的向量库：归一化向量做点积检索，线性扫描，适合中小规模。"""

    def __init__(self, embedder):
        self._embedder = embedder
        self.chunks: list[Chunk] = []
        self._vectors: np.ndarray | None = None  # shape=(n, dim)，已归一化

    def __len__(self) -> int:
        return len(self.chunks)

    # ---- 写入 ----
    def add(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        vectors = self._embedder.embed([c.text for c in chunks])
        vectors = self._normalize(vectors)
        if self._vectors is None:
            self._vectors = vectors
        else:
            self._vectors = np.vstack([self._vectors, vectors])
        self.chunks.extend(chunks)
        return len(chunks)

    # ---- 检索 ----
    def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.0,
        where: dict | None = None,
    ) -> list[SearchResult]:
        if self._vectors is None or len(self.chunks) == 0:
            return []

        q = self._normalize(self._embedder.embed_query(query)[None, :])[0]
        # 向量已归一化，点积 == 余弦。用 einsum 而非 `@`：
        # macOS Accelerate 的 BLAS gemm 会对矩阵-向量乘法虚假地抛
        # "divide by zero in matmul" RuntimeWarning，einsum 走独立路径可避免。
        scores = np.einsum("ij,j->i", self._vectors, q)

        mask = scores >= threshold
        if where:
            mask &= np.array([self._matches(c, where) for c in self.chunks])

        indices = np.where(mask)[0]
        order = indices[np.argsort(-scores[indices])]
        top = order[:top_k]
        return [SearchResult(self.chunks[int(i)], float(scores[int(i)])) for i in top]

    # ---- 删除 ----
    def delete_by_source(self, source: str) -> int:
        keep = [i for i, c in enumerate(self.chunks) if c.source != source]
        removed = len(self.chunks) - len(keep)
        self.chunks = [self.chunks[i] for i in keep]
        if self._vectors is not None:
            self._vectors = self._vectors[keep] if keep else None
        return removed

    def clear(self) -> None:
        self.chunks = []
        self._vectors = None

    # ---- 持久化 ----
    def save(self, dirpath: str) -> None:
        os.makedirs(dirpath, exist_ok=True)
        vectors = self._vectors if self._vectors is not None else np.zeros((0, 1), dtype="float32")
        np.save(os.path.join(dirpath, "vectors.npy"), vectors)
        with open(os.path.join(dirpath, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump([c.to_dict() for c in self.chunks], f, ensure_ascii=False)

    def load(self, dirpath: str) -> bool:
        vectors_path = os.path.join(dirpath, "vectors.npy")
        chunks_path = os.path.join(dirpath, "chunks.json")
        if not (os.path.exists(vectors_path) and os.path.exists(chunks_path)):
            return False
        self._vectors = np.load(vectors_path)
        with open(chunks_path, encoding="utf-8") as f:
            self.chunks = [Chunk.from_dict(d) for d in json.load(f)]
        return True

    # ---- 内部 ----
    @staticmethod
    def _normalize(vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    @staticmethod
    def _matches(chunk: Chunk, where: dict) -> bool:
        for key, value in where.items():
            if key in chunk.metadata:
                actual = chunk.metadata[key]
            else:
                actual = getattr(chunk, key, None)
            if actual != value:
                return False
        return True
