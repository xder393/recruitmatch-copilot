"""Tenant-scoped retrieval contracts prepared for the pgvector adapter."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


RECRUITING_SOURCE_TYPES = frozenset({"resume", "job_version", "knowledge_document"})
EMBEDDING_DIMENSION = 512
EMBEDDING_NORM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class SearchScope:
    tenant_id: str
    source_types: frozenset[str]
    authorized_sources: frozenset[tuple[str, str, str]]


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
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


def validate_scope(scope: SearchScope) -> None:
    if not scope.tenant_id.strip():
        raise ValueError("tenant_id must be non-empty")
    if not scope.source_types:
        raise ValueError("at least one source type is required")
    if not scope.source_types <= RECRUITING_SOURCE_TYPES:
        raise ValueError("scope contains an unsupported source type")
    for source_type, source_id, source_version in scope.authorized_sources:
        if source_type not in scope.source_types:
            raise ValueError("authorized source type must be included in source_types")
        if not source_id.strip() or not source_version.strip():
            raise ValueError("authorized source id and version must be non-empty")


def validate_search_request(
    scope: SearchScope,
    query_embedding: list[float],
    embedding_model: str,
    top_k: int,
    min_score: float,
) -> None:
    validate_scope(scope)
    if not embedding_model.strip():
        raise ValueError("embedding model must be non-empty")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if isinstance(min_score, bool) or not math.isfinite(min_score) or not -1.0 <= min_score <= 1.0:
        raise ValueError("min_score must be finite and between -1 and 1")
    if len(query_embedding) != EMBEDDING_DIMENSION:
        raise ValueError(f"query embedding must contain exactly {EMBEDDING_DIMENSION} values")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in query_embedding):
        raise ValueError("query embedding values must be numeric")
    if not all(math.isfinite(value) for value in query_embedding):
        raise ValueError("query embedding values must be finite")
    norm = math.sqrt(sum(value * value for value in query_embedding))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=EMBEDDING_NORM_TOLERANCE):
        raise ValueError("query embedding must be L2-normalized")


def validate_citation_ids(citation_ids: frozenset[str]) -> None:
    if any(not citation_id.strip() for citation_id in citation_ids):
        raise ValueError("citation IDs must be non-empty")


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
