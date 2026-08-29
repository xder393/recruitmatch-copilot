"""Deterministic in-memory implementation of the recruiting retrieval port."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from operator import attrgetter

from sqlalchemy import select

from app.domain.enums import JobStatus, ResumeStatus
from app.models.jobs import Job, JobVersion
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume, ResumeArtifact
from app.retrieval.generations import (
    GenerationConflictError,
    GenerationValidationError,
    GenerationWriter,
    IndexFailureCode,
    SourceNotFoundError,
    SourceRef,
    StagedChunk,
)
from app.retrieval.indexing import SourceIndexer

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
    source_search_index_status: str = "ready"
    start_offset: int = 0
    end_offset: int = 1
    page_number: int | None = None
    section: str | None = None
    privacy_deleted: bool = False
    source_exists: bool = True


class FakeRecruitingVectorIndex:
    def __init__(self, chunks: list[FakeRecruitingChunk] | None = None, session_factory=None):
        self._chunks = list(chunks or [])
        self._session_factory = session_factory

    def replace_generation(self, source: SourceRef, generation: int, chunks: list[StagedChunk]) -> None:
        self._chunks = [
            replace(item, is_active=False)
            if (item.tenant_id, item.source_type, item.source_id, item.source_version)
            == (source.tenant_id, source.source_type, source.source_id, source.source_version)
            else item
            for item in self._chunks
        ]
        self._chunks.extend(
            FakeRecruitingChunk(
                id=item.citation_id,
                tenant_id=source.tenant_id,
                citation_id=item.citation_id,
                source_type=source.source_type,
                source_id=source.source_id,
                source_version=source.source_version,
                generation=generation,
                active_source_generation=generation,
                embedding=item.embedding,
                embedding_model=item.embedding_model,
                is_active=True,
                content=item.content,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
                page_number=item.page_number,
                section=item.section,
            )
            for item in chunks
        )

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
            active_generation, search_status = self._source_state(chunk)
            if (
                chunk.source_type not in scope.source_types
                or (chunk.source_type, chunk.source_id, chunk.source_version) not in scope.authorized_sources
                or not chunk.is_active
                or chunk.generation != active_generation
                or chunk.embedding_model != embedding_model
                or search_status != "ready"
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
            if chunk.citation_id not in citation_ids or not self._is_resolvable(
                chunk, tenant_id, allow_inactive=not active_only
            ):
                continue
            active_generation, search_status = self._source_state(chunk)
            if scope is not None and (
                chunk.source_type not in scope.source_types
                or (chunk.source_type, chunk.source_id, chunk.source_version) not in scope.authorized_sources
            ):
                continue
            if active_only and (
                not chunk.is_active or chunk.generation != active_generation or search_status != "ready"
            ):
                continue
            hits.append(self._result(chunk, 1.0))
        return sorted(hits, key=lambda hit: (hit.id, hit.citation_id))

    def _is_resolvable(self, chunk: FakeRecruitingChunk, tenant_id: str, *, allow_inactive: bool = False) -> bool:
        _, status = self._source_state(chunk)
        return (
            chunk.tenant_id == tenant_id
            and chunk.source_exists
            and status != "deleted"
            and (allow_inactive or status != "inactive")
            and not chunk.privacy_deleted
            and bool(chunk.content)
        )

    def _source_state(self, chunk: FakeRecruitingChunk) -> tuple[int, str]:
        if self._session_factory is None:
            return chunk.active_source_generation, chunk.source_search_index_status
        source = SourceRef(chunk.tenant_id, chunk.source_type, chunk.source_id, chunk.source_version)
        with self._session_factory() as session:
            row = FakeGenerationWriter._source(session, source)
            if row is None:
                return -1, "deleted"
            if source.source_type == "resume" and row.status is ResumeStatus.DELETED:
                return row.active_index_generation, "deleted"
            if source.source_type == "job_version" and row.job.status is JobStatus.INACTIVE:
                return row.active_index_generation, "inactive"
            if source.source_type == "knowledge_document" and row.status in {"inactive", "deleted"}:
                return row.active_index_generation, row.status
            return row.active_index_generation, row.search_index_status

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


class FakeGenerationWriter:
    """SQLite test writer with the same Source authority effects as production."""

    def __init__(self, session_factory, index: FakeRecruitingVectorIndex, *, activation_checkpoint=None):
        self.session_factory = session_factory
        self.index = index
        self.activation_checkpoint = activation_checkpoint
        self.staged: dict[tuple[SourceRef, int], list[StagedChunk]] = {}

    def stage(self, source, generation, chunks, *, fencing_token=None):
        GenerationWriter._reject_unverifiable_fencing(fencing_token)
        GenerationWriter._validate_generation_number(generation)
        GenerationWriter._validate_chunks(chunks)
        with self.session_factory() as session:
            row = self._source(session, source)
            if not self._available(row, source):
                raise SourceNotFoundError("tenant-qualified Source is missing or privacy-deleted")
            if generation != row.active_index_generation + 1:
                raise GenerationConflictError("requested generation is not the Source's current next generation")
            for chunk in chunks:
                if (
                    source.source_type == "knowledge_document"
                    and chunk.document_id is not None
                    and chunk.document_id != source.source_id
                ):
                    raise GenerationValidationError("knowledge cleanup key must equal Source ID")
                if source.source_type == "job_version" and chunk.document_id is not None:
                    raise GenerationValidationError("job-version chunks cannot carry a cleanup key")
                if source.source_type == "resume" and chunk.document_id is not None:
                    owned = session.scalar(
                        select(ResumeArtifact.id).where(
                            ResumeArtifact.id == chunk.document_id,
                            ResumeArtifact.resume_id == source.source_id,
                        )
                    )
                    if owned is None:
                        raise GenerationValidationError("resume cleanup key must identify its Source artifact")
        existing = self.staged.get((source, generation))
        if existing is not None:
            ordering = attrgetter("start_offset", "end_offset", "citation_id")
            if sorted(existing, key=ordering) != sorted(chunks, key=ordering):
                raise GenerationValidationError("existing staged generation is partial or mismatched")
            return len(existing)
        citation_ids = {item.citation_id for item in chunks}
        all_existing = {item.citation_id for item in self.index._chunks if item.tenant_id == source.tenant_id}
        all_existing.update(
            item.citation_id
            for (staged_source, _), values in self.staged.items()
            if staged_source.tenant_id == source.tenant_id
            for item in values
        )
        if citation_ids & all_existing:
            raise GenerationValidationError("citation ID already exists for this tenant")
        self.staged[(source, generation)] = list(chunks)
        return len(chunks)

    def activate(self, source, generation, *, expected_count, embedding_model, fencing_token=None):
        GenerationWriter._reject_unverifiable_fencing(fencing_token)
        GenerationWriter._validate_generation_number(generation)
        chunks = self.staged.get((source, generation), [])
        if len(chunks) != expected_count:
            raise GenerationValidationError("staged generation is incomplete")
        GenerationWriter._validate_chunks(chunks)
        if any(item.embedding_model != embedding_model for item in chunks):
            raise GenerationValidationError("staged generation is incomplete")
        with self.session_factory() as session:
            row = self._source(session, source)
            if not self._available(row, source):
                raise SourceNotFoundError("source is unavailable")
            if generation != row.active_index_generation + 1:
                raise GenerationConflictError("requested generation is not the Source's current next generation")
            if self.activation_checkpoint is not None:
                self.activation_checkpoint()
            row.active_index_generation = generation
            row.search_index_status = "ready"
            row.search_index_error_code = None
            row.search_indexed_at = datetime.now(timezone.utc)
            session.commit()
        self.index.replace_generation(source, generation, chunks)

    def fail(self, source, error_code: IndexFailureCode, *, fencing_token=None):
        GenerationWriter._reject_unverifiable_fencing(fencing_token)
        if not isinstance(error_code, IndexFailureCode):
            raise GenerationValidationError("index failure code must be a stable IndexFailureCode value")
        with self.session_factory() as session:
            row = self._source(session, source)
            if not self._available(row, source):
                raise SourceNotFoundError("source is unavailable")
            row.search_index_status = "ready" if row.active_index_generation > 0 else "failed"
            row.search_index_error_code = error_code.value
            session.commit()

    @staticmethod
    def _available(row, source) -> bool:
        if row is None or row.search_index_status == "deleted":
            return False
        if source.source_type == "resume":
            return row.status is not ResumeStatus.DELETED and row.deleted_at is None
        if source.source_type == "knowledge_document":
            return row.status != "deleted"
        return True

    @staticmethod
    def _source(session, source):
        if source.source_type == "resume":
            return session.scalar(
                select(Resume).where(
                    Resume.tenant_id == source.tenant_id,
                    Resume.id == source.source_id,
                    Resume.sha256 == source.source_version,
                )
            )
        if source.source_type == "job_version":
            return session.scalar(
                select(JobVersion)
                .join(Job, Job.id == JobVersion.job_id)
                .where(
                    Job.tenant_id == source.tenant_id,
                    JobVersion.id == source.source_id,
                    JobVersion.version == int(source.source_version),
                )
            )
        return session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == source.tenant_id,
                KnowledgeDocument.id == source.source_id,
                KnowledgeDocument.checksum == source.source_version,
            )
        )


def sqlite_retrieval_dependencies(database_url: str, embedder):
    from app.database import create_engine_and_session

    _, session_factory = create_engine_and_session(database_url)
    index = FakeRecruitingVectorIndex(session_factory=session_factory)
    writer = FakeGenerationWriter(session_factory, index)
    return index, SourceIndexer(writer, embedder)
