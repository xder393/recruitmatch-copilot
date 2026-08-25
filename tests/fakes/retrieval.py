"""Deterministic in-memory implementation of the recruiting retrieval port."""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.ports import (
    RetrievedChunk,
    SearchScope,
    validate_citation_ids,
    validate_scope,
    validate_search_request,
)


@dataclass(frozen=True)
class FakeRecruitingChunk:
    id: str
    tenant_id: str
    citation_id: str
    source_type: str
    source_id: str
    source_version: str
    generation: int
    active_source_generation: int
    embedding: list[float]
    embedding_model: str
    is_active: bool
    content: str
    start_offset: int = 0
    end_offset: int = 1
    page_number: int | None = None
    section: str | None = None
    privacy_deleted: bool = False
    source_exists: bool = True


class FakeRecruitingVectorIndex:
    def __init__(self, chunks: list[FakeRecruitingChunk]):
        self._chunks = tuple(chunks)

    def search(
        self,
        scope: SearchScope,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
        min_score: float,
    ) -> list[RetrievedChunk]:
        validate_search_request(scope, query_embedding, embedding_model, top_k, min_score)
        if not scope.authorized_sources:
            return []
        hits: list[RetrievedChunk] = []
        for chunk in self._chunks:
            if not self._is_resolvable(chunk, scope.tenant_id):
                continue
            if (
                chunk.source_type not in scope.source_types
                or (chunk.source_type, chunk.source_id, chunk.source_version) not in scope.authorized_sources
                or not chunk.is_active
                or chunk.generation != chunk.active_source_generation
                or chunk.embedding_model != embedding_model
            ):
                continue
            score = sum(left * right for left, right in zip(query_embedding, chunk.embedding, strict=True))
            if score >= min_score:
                hits.append(self._result(chunk, score))
        return sorted(hits, key=lambda hit: (-hit.score, hit.id))[:top_k]

    def resolve_active_citations(
        self,
        scope: SearchScope,
        citation_ids: frozenset[str],
    ) -> list[RetrievedChunk]:
        validate_scope(scope)
        validate_citation_ids(citation_ids)
        if not scope.authorized_sources or not citation_ids:
            return []
        return self._resolve(scope.tenant_id, citation_ids, scope=scope, active_only=True)

    def resolve_historical_citations(
        self,
        tenant_id: str,
        citation_ids: frozenset[str],
    ) -> list[RetrievedChunk]:
        if not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")
        validate_citation_ids(citation_ids)
        if not citation_ids:
            return []
        return self._resolve(tenant_id, citation_ids, scope=None, active_only=False)

    def _resolve(
        self,
        tenant_id: str,
        citation_ids: frozenset[str],
        *,
        scope: SearchScope | None,
        active_only: bool,
    ) -> list[RetrievedChunk]:
        hits: list[RetrievedChunk] = []
        for chunk in self._chunks:
            if chunk.citation_id not in citation_ids or not self._is_resolvable(chunk, tenant_id):
                continue
            if scope is not None and (
                chunk.source_type not in scope.source_types
                or (chunk.source_type, chunk.source_id, chunk.source_version) not in scope.authorized_sources
            ):
                continue
            if active_only and (not chunk.is_active or chunk.generation != chunk.active_source_generation):
                continue
            hits.append(self._result(chunk, 1.0))
        return sorted(hits, key=lambda hit: (hit.id, hit.citation_id))

    @staticmethod
    def _is_resolvable(chunk: FakeRecruitingChunk, tenant_id: str) -> bool:
        return (
            chunk.tenant_id == tenant_id and chunk.source_exists and not chunk.privacy_deleted and bool(chunk.content)
        )

    @staticmethod
    def _result(chunk: FakeRecruitingChunk, score: float) -> RetrievedChunk:
        return RetrievedChunk(
            id=chunk.id,
            tenant_id=chunk.tenant_id,
            citation_id=chunk.citation_id,
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            source_version=chunk.source_version,
            generation=chunk.generation,
            content=chunk.content,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            page_number=chunk.page_number,
            section=chunk.section,
            score=score,
        )
