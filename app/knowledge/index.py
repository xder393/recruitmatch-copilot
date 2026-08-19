"""Persistent tenant-first recruiting vector retrieval."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Protocol, Set

import numpy as np
from sqlalchemy import select, update

from app.knowledge.schemas import ChunkInput
from app.models.knowledge import KnowledgeChunk


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class RetrievedChunk:
    citation_id: str
    source_type: str
    source_id: str
    content: str
    start: int
    end: int
    page: Optional[int]
    score: float


def _citation_id(chunk: KnowledgeChunk) -> str:
    raw = "|".join(
        [
            chunk.tenant_id,
            chunk.source_type,
            chunk.source_id,
            chunk.source_version,
            str(chunk.start),
            str(chunk.end),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class RecruitingVectorIndex:
    def __init__(self, session_factory, embedder: Embedder):
        self.session_factory = session_factory
        self.embedder = embedder

    def embed(self, chunks: list[ChunkInput]) -> list[ChunkInput]:
        if not chunks:
            return []
        vectors = self.embedder.embed_documents([item.content for item in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("embedding count mismatch")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or 0 in dimensions:
            raise ValueError("embedding dimension mismatch")
        return [item.model_copy(update={"vector": vector}) for item, vector in zip(chunks, vectors)]

    def index_source(
        self,
        tenant_id: str,
        source_type: str,
        source_id: str,
        source_version: str,
        chunks: list[ChunkInput],
    ) -> None:
        embedded = self.embed(chunks)
        with self.session_factory() as session:
            session.execute(
                update(KnowledgeChunk)
                .where(
                    KnowledgeChunk.tenant_id == tenant_id,
                    KnowledgeChunk.source_type == source_type,
                    KnowledgeChunk.source_id == source_id,
                    KnowledgeChunk.is_active.is_(True),
                )
                .values(is_active=False)
            )
            for item in embedded:
                session.add(
                    KnowledgeChunk(
                        tenant_id=tenant_id,
                        document_id=None,
                        source_type=source_type,
                        source_id=source_id,
                        source_version=source_version,
                        generation=1,
                        start=item.start,
                        end=item.end,
                        page=item.page,
                        content=item.content,
                        vector=item.vector,
                        is_active=True,
                    )
                )
            session.commit()

    def index_generation(
        self,
        tenant_id: str,
        document_id: str,
        generation: int,
        chunks: list[ChunkInput],
    ) -> None:
        self.index_source(tenant_id, "policy", document_id, str(generation), chunks)

    def search(
        self,
        tenant_id: str,
        query: str,
        source_types: Set[str],
        top_k: int,
        min_score: float,
    ) -> list[RetrievedChunk]:
        if not source_types or top_k <= 0:
            return []
        with self.session_factory() as session:
            chunks = list(
                session.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.tenant_id == tenant_id,
                        KnowledgeChunk.source_type.in_(source_types),
                        KnowledgeChunk.is_active.is_(True),
                    )
                )
            )
        if not chunks:
            return []
        query_vector = np.asarray(self.embedder.embed_query(query), dtype="float32")
        query_norm = float(np.linalg.norm(query_vector))
        if not query_norm:
            return []
        scored = []
        for chunk in chunks:
            vector = np.asarray(chunk.vector, dtype="float32")
            denominator = query_norm * float(np.linalg.norm(vector))
            score = float(np.dot(query_vector, vector) / denominator) if denominator else 0.0
            if score >= min_score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            RetrievedChunk(
                citation_id=_citation_id(chunk),
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                content=chunk.content,
                start=chunk.start,
                end=chunk.end,
                page=chunk.page,
                score=round(score, 6),
            )
            for score, chunk in scored[:top_k]
        ]

    def source_chunks(self, tenant_id: str, source_type: str, source_id: str) -> list[RetrievedChunk]:
        """Return active chunks for one tenant-owned source without semantic broadening."""
        with self.session_factory() as session:
            chunks = list(
                session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.tenant_id == tenant_id,
                        KnowledgeChunk.source_type == source_type,
                        KnowledgeChunk.source_id == source_id,
                        KnowledgeChunk.is_active.is_(True),
                    )
                    .order_by(KnowledgeChunk.start, KnowledgeChunk.id)
                )
            )
        return [
            RetrievedChunk(
                citation_id=_citation_id(chunk),
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                content=chunk.content,
                start=chunk.start,
                end=chunk.end,
                page=chunk.page,
                score=1.0,
            )
            for chunk in chunks
        ]
