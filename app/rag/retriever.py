"""检索层：封装 VectorStore，统一 top_k / 阈值，提供上下文拼接。"""

from __future__ import annotations

from app.config import Settings
from app.storage.vector_store import SearchResult, VectorStore


def format_context(results: list[SearchResult]) -> str:
    """把检索结果拼成给 LLM 的上下文，并带上来源标注。"""
    blocks = []
    for r in results:
        loc = r.chunk.source
        if r.chunk.page is not None:
            loc += f"（第 {r.chunk.page} 页）"
        blocks.append(f"[来源: {loc}]\n{r.chunk.text}")
    return "\n\n".join(blocks)


class Retriever:
    """对 VectorStore 的一层薄封装，统一检索参数与空库处理。"""

    def __init__(self, store: VectorStore, settings: Settings):
        self.store = store
        self.settings = settings

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
        where: dict | None = None,
    ) -> list[SearchResult]:
        return self.store.search(
            query,
            top_k=top_k or self.settings.top_k,
            threshold=self.settings.similarity_threshold if threshold is None else threshold,
            where=where,
        )

    def count(self) -> int:
        return len(self.store)
