"""Deterministic exact-offset knowledge chunking."""

from __future__ import annotations

from app.knowledge.schemas import ChunkInput


def chunk_document(
    text: str,
    chunk_size: int = 700,
    overlap: int = 100,
) -> list[ChunkInput]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("invalid chunk settings")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        content = text[start:end]
        if content:
            chunks.append(
                ChunkInput(
                    content=content,
                    start=start,
                    end=end,
                )
            )
        if end == len(text):
            break
        start = end - overlap
    return chunks
