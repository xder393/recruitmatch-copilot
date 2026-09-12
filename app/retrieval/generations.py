"""Atomic PostgreSQL generation staging and activation for recruiting evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Callable, TypeAlias
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import String, cast, select, update, delete, or_, exists, func
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.exc import IntegrityError, OperationalError, DisconnectionError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.models.jobs import Job, JobVersion
from app.models.knowledge import KnowledgeDocument
from app.models.resumes import Resume
from app.models.artifacts import Artifact
from app.models.retrieval import RecruitingChunk
from app.models.matching import MatchRun, MatchResult
from app.processing.leases import LeaseRepository
from app.processing.outcomes import ClaimedLease, LeaseOwnershipLost
from app.repositories.ports import PersistenceUnavailable
from app.retrieval.ports import EMBEDDING_DIMENSION, EMBEDDING_NORM_TOLERANCE, RECRUITING_SOURCE_TYPES


SourceRow: TypeAlias = Resume | JobVersion | KnowledgeDocument


class GenerationWriterError(RuntimeError):
    """Base class for generation boundary failures."""


class SourceNotFoundError(GenerationWriterError):
    """The exact tenant/type/id/version source is missing or privacy-deleted."""


class GenerationConflictError(GenerationWriterError):
    """The requested generation is no longer the source's next generation."""


class GenerationValidationError(GenerationWriterError):
    """Staged evidence is incomplete or violates the embedding contract."""


class FencingNotSupportedError(GenerationWriterError):
    """Synchronous JobVersion indexing does not accept processing leases."""


class IndexFailureCode(str, Enum):
    """Stable, bounded failure codes safe to persist on Source rows."""

    EMBEDDING_FAILED = "embedding_failed"
    VALIDATION_FAILED = "validation_failed"
    ACTIVATION_FAILED = "activation_failed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class SourceRef:
    """Immutable, tenant-qualified recruiting Source identity."""

    tenant_id: str
    source_type: str
    source_id: str
    source_version: str

    def __post_init__(self) -> None:
        if self.source_type not in RECRUITING_SOURCE_TYPES:
            raise ValueError("unsupported recruiting source type")
        if not self.tenant_id.strip() or not self.source_id.strip() or not self.source_version.strip():
            raise ValueError("source tenant, id, and immutable version must be non-empty")


@dataclass(frozen=True)
class StagedChunk:
    """One validated, initially inactive chunk for a future generation."""

    citation_id: str
    content: str
    start_offset: int
    end_offset: int
    embedding: list[float]
    embedding_model: str
    document_id: str | None = None
    page_number: int | None = None
    section: str | None = None


class GenerationWriter:
    """Stage and atomically switch Source-owned recruiting index generations."""

    MAX_RECONCILE_CHUNKS = 2048

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        activation_checkpoint: Callable[[], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.activation_checkpoint = activation_checkpoint

    def stage(
        self,
        source: SourceRef,
        generation: int,
        chunks: list[StagedChunk],
        *,
        fencing_token: ClaimedLease | None = None,
    ) -> int:
        self._validate_generation_number(generation)
        embedding_model = self._validate_chunks(chunks)

        try:
            with self._transaction(source, fencing_token) as session:
                source_row = self._lock_source(session, source)
                self._require_next_generation(source_row, generation)
                existing = list(
                    session.scalars(
                        select(RecruitingChunk)
                        .where(*self._chunk_scope(source), RecruitingChunk.generation == generation)
                        .order_by(RecruitingChunk.start_offset, RecruitingChunk.end_offset, RecruitingChunk.id)
                        .with_for_update()
                    )
                )
                if existing:
                    self._require_exact_replay(session, source, existing, chunks)
                    return len(existing)
                self._require_unique_citations(session, source, chunks)
                for chunk in chunks:
                    document_id = self._normalize_document_id(session, source, chunk.document_id)
                    session.add(
                        RecruitingChunk(
                            tenant_id=source.tenant_id,
                            document_id=document_id,
                            source_type=source.source_type,
                            source_id=source.source_id,
                            source_version=source.source_version,
                            generation=generation,
                            citation_id=chunk.citation_id,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            start_offset=chunk.start_offset,
                            end_offset=chunk.end_offset,
                            content=chunk.content,
                            embedding=chunk.embedding,
                            embedding_model=embedding_model,
                            is_active=False,
                        )
                    )
                session.flush()
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint_name == "uq_recruiting_chunk_tenant_citation":
                raise GenerationValidationError("citation ID already exists for this tenant") from None
            raise
        return len(chunks)

    def activate(
        self,
        source: SourceRef,
        generation: int,
        *,
        expected_count: int,
        embedding_model: str,
        fencing_token: ClaimedLease | None = None,
    ) -> None:
        self._validate_generation_number(generation)
        if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count <= 0:
            raise GenerationValidationError("expected staged count must be a positive integer")
        if not embedding_model.strip():
            raise GenerationValidationError("embedding model identity must be non-empty")

        with self._transaction(source, fencing_token) as session:
            source_row = self._lock_source(session, source)
            self._require_next_generation(source_row, generation)
            staged = list(
                session.scalars(
                    select(RecruitingChunk)
                    .where(*self._chunk_scope(source), RecruitingChunk.generation == generation)
                    .order_by(RecruitingChunk.start_offset, RecruitingChunk.end_offset, RecruitingChunk.id)
                    .with_for_update()
                )
            )
            if len(staged) != expected_count:
                raise GenerationValidationError("staged generation count does not match the expected complete count")
            self._validate_persisted_staging(staged, embedding_model)

            session.execute(
                update(RecruitingChunk)
                .where(*self._chunk_scope(source), RecruitingChunk.is_active.is_(True))
                .values(is_active=False)
            )
            if self.activation_checkpoint is not None:
                self.activation_checkpoint()
            session.execute(
                update(RecruitingChunk)
                .where(
                    *self._chunk_scope(source),
                    RecruitingChunk.generation == generation,
                    RecruitingChunk.is_active.is_(False),
                )
                .values(is_active=True)
            )
            source_row.active_index_generation = generation
            source_row.search_index_status = "ready"
            source_row.search_index_error_code = None
            source_row.search_indexed_at = datetime.now(timezone.utc)
            session.flush()

    def fail(
        self,
        source: SourceRef,
        error_code: IndexFailureCode,
        *,
        fencing_token: ClaimedLease | None = None,
    ) -> None:
        if not isinstance(error_code, IndexFailureCode):
            raise GenerationValidationError("index failure code must be a stable IndexFailureCode value")
        with self._transaction(source, fencing_token) as session:
            source_row = self._lock_source(session, source)
            source_row.search_index_status = "ready" if source_row.active_index_generation > 0 else "failed"
            source_row.search_index_error_code = error_code.value
            session.flush()

    @staticmethod
    def _validate_fencing(source: SourceRef, fencing_token: ClaimedLease | None) -> None:
        if source.source_type == "job_version":
            if fencing_token is not None:
                raise FencingNotSupportedError("job versions do not have processing leases")
        elif not isinstance(fencing_token, ClaimedLease) or (
            fencing_token.tenant_id,
            fencing_token.source_type,
            fencing_token.source_id,
        ) != (source.tenant_id, source.source_type, source.source_id):
            raise LeaseOwnershipLost("processing_lease_lost")

    @contextmanager
    def _transaction(self, source: SourceRef, fencing_token: ClaimedLease | None) -> Iterator[Session]:
        self._validate_fencing(source, fencing_token)
        try:
            with self.session_factory() as session, session.begin():
                if fencing_token is None:
                    yield session
                else:
                    with LeaseRepository(session).owned(fencing_token):
                        try:
                            yield session
                        except SourceNotFoundError:
                            raise LeaseOwnershipLost("processing_source_unavailable") from None
        except (OperationalError, DisconnectionError, PoolTimeoutError) as exc:
            raise PersistenceUnavailable("database_unavailable") from exc

    def reconcile_staging(self, source: SourceRef, generation: int, *, fencing_token: ClaimedLease) -> int:
        """A new claimant may reclaim bounded, unreferenced, never-published N+1."""
        self._validate_generation_number(generation)
        with self._transaction(source, fencing_token) as session:
            row = self._lock_source(session, source)
            self._require_next_generation(row, generation)
            future = list(
                session.scalars(
                    select(RecruitingChunk)
                    .where(*self._chunk_scope(source), RecruitingChunk.generation > row.active_index_generation)
                    .limit(self.MAX_RECONCILE_CHUNKS + 1)
                    .with_for_update()
                )
            )
            if len(future) > self.MAX_RECONCILE_CHUNKS:
                raise GenerationValidationError("staging_reconciliation_limit")
            if any(chunk.is_active or chunk.generation != generation for chunk in future):
                raise GenerationValidationError("staging_reconciliation_inconsistent")
            if not future:
                return 0
            # SQL-side EXISTS over JSON values: never materialize a tenant's
            # matching JSON, or depend on a serializer's escaping/whitespace.
            fields = (
                MatchResult.citations,
                MatchResult.evidence,
                MatchResult.grounded_explanation,
                MatchResult.interview_questions,
                MatchResult.dimension_scores,
                MatchResult.matched_items,
                MatchResult.missing_items,
                MatchResult.uncertain_items,
                MatchResult.risk_flags,
            )
            predicates = [
                func.jsonb_path_exists(
                    cast(field, JSONB),
                    cast("$.** ? (@ == $citation)", JSONPATH),
                    func.jsonb_build_object("citation", RecruitingChunk.citation_id),
                )
                for field in fields
            ]
            referenced = session.scalar(
                select(
                    exists(
                        select(MatchResult.id)
                        .select_from(MatchResult)
                        .join(MatchRun)
                        .join(RecruitingChunk, or_(*predicates))
                        .where(
                            MatchRun.tenant_id == source.tenant_id,
                            RecruitingChunk.id.in_([chunk.id for chunk in future]),
                        )
                    )
                )
            )
            if referenced:
                raise GenerationValidationError("staging_reconciliation_referenced")
            session.execute(
                delete(RecruitingChunk).where(
                    *self._chunk_scope(source),
                    RecruitingChunk.generation == generation,
                    RecruitingChunk.is_active.is_(False),
                    RecruitingChunk.id.in_([chunk.id for chunk in future]),
                )
            )
            return len(future)

    @staticmethod
    def _validate_generation_number(generation: int) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise GenerationValidationError("generation must be a positive integer")

    @classmethod
    def _validate_chunks(cls, chunks: list[StagedChunk]) -> str:
        if not chunks:
            raise GenerationValidationError("at least one complete chunk is required")
        citation_ids: set[str] = set()
        offsets: set[tuple[int, int]] = set()
        embedding_models: set[str] = set()
        for chunk in chunks:
            cls._validate_chunk(chunk)
            if chunk.citation_id in citation_ids:
                raise GenerationValidationError("citation IDs must be unique within a staged generation")
            offset = (chunk.start_offset, chunk.end_offset)
            if offset in offsets:
                raise GenerationValidationError("chunk offsets must be unique within a staged generation")
            citation_ids.add(chunk.citation_id)
            offsets.add(offset)
            embedding_models.add(chunk.embedding_model)
        if len(embedding_models) != 1:
            raise GenerationValidationError("a staged generation must use exactly one embedding identity")
        return next(iter(embedding_models))

    @staticmethod
    def _validate_chunk(chunk: StagedChunk) -> None:
        if not chunk.citation_id.strip():
            raise GenerationValidationError("citation ID must be non-empty")
        if not chunk.content.strip():
            raise GenerationValidationError("chunk content must be non-empty")
        if (
            isinstance(chunk.start_offset, bool)
            or isinstance(chunk.end_offset, bool)
            or not isinstance(chunk.start_offset, int)
            or not isinstance(chunk.end_offset, int)
            or chunk.start_offset < 0
            or chunk.end_offset <= chunk.start_offset
        ):
            raise GenerationValidationError("chunk offsets must define a positive canonical text interval")
        if not chunk.embedding_model.strip():
            raise GenerationValidationError("embedding model identity must be non-empty")
        if len(chunk.embedding) != EMBEDDING_DIMENSION:
            raise GenerationValidationError(f"embedding must contain exactly {EMBEDDING_DIMENSION} values")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in chunk.embedding):
            raise GenerationValidationError("embedding values must be numeric")
        if not all(math.isfinite(value) for value in chunk.embedding):
            raise GenerationValidationError("embedding values must be finite")
        norm = math.sqrt(sum(value * value for value in chunk.embedding))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=EMBEDDING_NORM_TOLERANCE):
            raise GenerationValidationError("embedding must be L2-normalized")
        if chunk.page_number is not None and chunk.page_number <= 0:
            raise GenerationValidationError("page number must be positive when present")
        if chunk.section is not None and not chunk.section.strip():
            raise GenerationValidationError("section must be non-empty when present")
        if chunk.document_id is not None and not chunk.document_id.strip():
            raise GenerationValidationError("document ID must be non-empty when present")

    @classmethod
    def _validate_persisted_staging(cls, chunks: list[RecruitingChunk], embedding_model: str) -> None:
        if not chunks:
            raise GenerationValidationError("the requested generation has no staged chunks")
        inputs = [
            StagedChunk(
                citation_id=chunk.citation_id,
                content=chunk.content,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                embedding=list(chunk.embedding),
                embedding_model=chunk.embedding_model,
                document_id=chunk.document_id,
                page_number=chunk.page_number,
                section=chunk.section,
            )
            for chunk in chunks
        ]
        staged_model = cls._validate_chunks(inputs)
        if staged_model != embedding_model:
            raise GenerationValidationError("staged embedding identity does not match activation identity")
        if any(chunk.is_active for chunk in chunks):
            raise GenerationValidationError("all staged chunks must remain inactive before activation")

    @staticmethod
    def _require_next_generation(source: SourceRow, requested_generation: int) -> None:
        if requested_generation != source.active_index_generation + 1:
            raise GenerationConflictError("requested generation is not the Source's current next generation")

    @classmethod
    def _require_unique_citations(
        cls,
        session: Session,
        source: SourceRef,
        chunks: list[StagedChunk],
    ) -> None:
        citation_ids = [chunk.citation_id for chunk in chunks]
        if (
            session.scalar(
                select(RecruitingChunk.id).where(
                    RecruitingChunk.tenant_id == source.tenant_id,
                    RecruitingChunk.citation_id.in_(citation_ids),
                )
            )
            is not None
        ):
            raise GenerationValidationError("citation ID already exists for this tenant")

    @classmethod
    def _require_exact_replay(
        cls,
        session: Session,
        source: SourceRef,
        existing: list[RecruitingChunk],
        chunks: list[StagedChunk],
    ) -> None:
        if len(existing) != len(chunks):
            raise GenerationValidationError("existing staged generation is partial or mismatched")
        ordered = sorted(chunks, key=lambda item: (item.start_offset, item.end_offset, item.citation_id))
        for persisted, proposed in zip(existing, ordered, strict=True):
            document_id = cls._normalize_document_id(session, source, proposed.document_id)
            if (
                persisted.citation_id != proposed.citation_id
                or persisted.content != proposed.content
                or persisted.start_offset != proposed.start_offset
                or persisted.end_offset != proposed.end_offset
                or list(persisted.embedding) != list(proposed.embedding)
                or persisted.embedding_model != proposed.embedding_model
                or persisted.document_id != document_id
                or persisted.page_number != proposed.page_number
                or persisted.section != proposed.section
                or persisted.is_active
            ):
                raise GenerationValidationError("existing staged generation is partial or mismatched")

    @staticmethod
    def _chunk_scope(source: SourceRef) -> tuple[ColumnElement[bool], ...]:
        return (
            RecruitingChunk.tenant_id == source.tenant_id,
            RecruitingChunk.source_type == source.source_type,
            RecruitingChunk.source_id == source.source_id,
            RecruitingChunk.source_version == source.source_version,
        )

    @staticmethod
    def _normalize_document_id(session: Session, source: SourceRef, document_id: str | None) -> str | None:
        if source.source_type == "knowledge_document":
            if document_id is not None and document_id != source.source_id:
                raise GenerationValidationError("knowledge document cleanup key must equal the locked Source ID")
            return source.source_id
        if source.source_type == "job_version":
            if document_id is not None:
                raise GenerationValidationError("job-version chunks cannot carry a document cleanup key")
            return None
        if document_id is None:
            return None
        owned_artifact_id = session.scalar(
            select(Artifact.id)
            .join(Resume, Resume.artifact_id == Artifact.id)
            .where(
                Artifact.id == document_id,
                Artifact.tenant_id == source.tenant_id,
                Artifact.owner_type == "resume",
                Artifact.owner_id == source.source_id,
                Resume.id == source.source_id,
                Resume.tenant_id == source.tenant_id,
            )
            .with_for_update(of=Artifact)
        )
        if owned_artifact_id is None:
            raise GenerationValidationError("resume document cleanup key must identify the locked Source artifact")
        return owned_artifact_id

    @staticmethod
    def _lock_source(session: Session, source: SourceRef) -> SourceRow:
        row: SourceRow | None
        if source.source_type == "resume":
            row = session.scalar(
                select(Resume)
                .where(
                    Resume.tenant_id == source.tenant_id,
                    Resume.id == source.source_id,
                    Resume.sha256 == source.source_version,
                    Resume.deleted_at.is_(None),
                    Resume.lifecycle_status == "active",
                    Resume.search_index_status != "deleted",
                )
                .with_for_update(of=Resume)
            )
        elif source.source_type == "job_version":
            row = session.scalar(
                select(JobVersion)
                .join(Job, Job.id == JobVersion.job_id)
                .where(
                    Job.tenant_id == source.tenant_id,
                    JobVersion.id == source.source_id,
                    cast(JobVersion.version, String) == source.source_version,
                    JobVersion.search_index_status != "deleted",
                )
                .with_for_update(of=(Job, JobVersion))
            )
        else:
            row = session.scalar(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == source.tenant_id,
                    KnowledgeDocument.id == source.source_id,
                    KnowledgeDocument.checksum == source.source_version,
                    KnowledgeDocument.lifecycle_status == "active",
                    KnowledgeDocument.status != "inactive",
                    KnowledgeDocument.search_index_status != "deleted",
                )
                .with_for_update(of=KnowledgeDocument)
            )
        if row is None:
            raise SourceNotFoundError("tenant-qualified Source is missing or privacy-deleted")
        return row


class TransactionGenerationWriter(GenerationWriter):
    """UoW publication adapter: uses its caller's guarded transaction, never commits."""

    def __init__(self, session: Session):
        self._session = session
        self.activation_checkpoint = None

    @contextmanager
    def _transaction(self, source: SourceRef, fencing_token: ClaimedLease | None) -> Iterator[Session]:
        self._validate_fencing(source, fencing_token)
        if fencing_token is None or self._session.info.get("processing_lease_guard") != fencing_token:
            raise LeaseOwnershipLost("processing_publication_requires_guard")
        try:
            yield self._session
        except SourceNotFoundError:
            raise LeaseOwnershipLost("processing_source_unavailable") from None
