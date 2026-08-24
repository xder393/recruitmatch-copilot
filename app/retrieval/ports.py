"""Tenant-scoped retrieval contracts prepared for the pgvector adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchScope:
    tenant_id: str
    source_types: frozenset[str]
    authorized_sources: frozenset[tuple[str, str, str]]


@dataclass(frozen=True)
class RetrievedChunk:
    tenant_id: str
    citation_id: str
    source_type: str
    source_id: str
    source_version: str
    generation: int
    content: str
    start_offset: int
    end_offset: int
    page_number: int | None
    section: str | None
    score: float


class RecruitingVectorIndex(Protocol):
    def search(
        self,
        scope: SearchScope,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
        min_score: float,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError

    def resolve_active_citations(
        self,
        scope: SearchScope,
        citation_ids: frozenset[str],
    ) -> list[RetrievedChunk]:
        raise NotImplementedError

    def resolve_historical_citations(
        self,
        tenant_id: str,
        citation_ids: frozenset[str],
    ) -> list[RetrievedChunk]:
        raise NotImplementedError


class VectorIndexHealthProbe(Protocol):
    def probe(self) -> bool:
        raise NotImplementedError
