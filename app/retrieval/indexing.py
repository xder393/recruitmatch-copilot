"""Complete-generation indexing orchestration shared by API and workers."""

from __future__ import annotations

import hashlib
from app.processing.outcomes import ClaimedLease, LeaseOwnershipLost
from typing import Protocol

from app.knowledge.schemas import ChunkInput
from app.retrieval.generations import (
    GenerationConflictError,
    GenerationValidationError,
    IndexFailureCode,
    SourceNotFoundError,
    SourceRef,
    StagedChunk,
)


def classify_index_failure(error: Exception) -> IndexFailureCode:
    """Map internal indexing exceptions to the bounded persisted taxonomy."""
    if isinstance(error, LeaseOwnershipLost):
        return IndexFailureCode.LEASE_LOST
    if isinstance(error, GenerationValidationError | ValueError):
        return IndexFailureCode.VALIDATION_FAILED
    if isinstance(error, GenerationConflictError):
        return IndexFailureCode.ACTIVATION_FAILED
    if isinstance(error, SourceNotFoundError):
        return IndexFailureCode.SOURCE_UNAVAILABLE
    return IndexFailureCode.EMBEDDING_FAILED


class EmbeddingAdapter(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingFailure(RuntimeError):
    """Only an external embedding call failed, before any persistence operation."""


class GenerationPublicationPort(Protocol):
    def activate(
        self,
        source: SourceRef,
        generation: int,
        *,
        expected_count: int,
        embedding_model: str,
        fencing_token=None,
    ) -> None: ...

    def fail(self, source: SourceRef, error_code: IndexFailureCode, *, fencing_token=None) -> None: ...


class GenerationWriterPort(GenerationPublicationPort, Protocol):
    def reconcile_staging(self, source: SourceRef, generation: int, *, fencing_token: ClaimedLease) -> int: ...
    def stage(self, source: SourceRef, generation: int, chunks: list[StagedChunk], *, fencing_token=None) -> int: ...


class SourceIndexer:
    """Embed every chunk, then stage and activate one complete generation."""

    def __init__(self, writer: GenerationWriterPort, embedder: EmbeddingAdapter) -> None:
        self.writer = writer
        self.embedder = embedder

    def index(
        self,
        source: SourceRef,
        generation: int,
        chunks: list[ChunkInput],
        *,
        document_id: str | None = None,
        fencing_token: ClaimedLease | None = None,
    ) -> int:
        count = self.stage(source, generation, chunks, document_id=document_id, fencing_token=fencing_token)
        self.writer.activate(
            source,
            generation,
            expected_count=count,
            embedding_model=self.embedder.model_name,
            fencing_token=fencing_token,
        )
        return count

    def stage(
        self,
        source: SourceRef,
        generation: int,
        chunks: list[ChunkInput],
        *,
        document_id: str | None = None,
        fencing_token: ClaimedLease | None = None,
    ) -> int:
        if not chunks:
            raise ValueError("at least one source chunk is required")
        try:
            vectors = self.embedder.embed_documents([chunk.content for chunk in chunks])
        except (ValueError, LeaseOwnershipLost):
            raise
        except Exception as exc:
            raise EmbeddingFailure("embedding_failed") from exc
        if len(vectors) != len(chunks) or any(not vector for vector in vectors):
            raise ValueError("embedding adapter must return one complete vector per chunk")
        staged = [
            StagedChunk(
                citation_id=self._citation_id(source, generation, chunk.start, chunk.end),
                content=chunk.content,
                start_offset=chunk.start,
                end_offset=chunk.end,
                embedding=vector,
                embedding_model=self.embedder.model_name,
                document_id=document_id,
                page_number=chunk.page,
                section=None,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        return self.writer.stage(source, generation, staged, fencing_token=fencing_token)

    def reconcile_staging(self, source: SourceRef, generation: int, *, fencing_token: ClaimedLease) -> int:
        return self.writer.reconcile_staging(source, generation, fencing_token=fencing_token)

    def fail(
        self,
        source: SourceRef,
        error_code: IndexFailureCode,
        *,
        fencing_token: ClaimedLease | None = None,
    ) -> None:
        self.writer.fail(source, error_code, fencing_token=fencing_token)

    @staticmethod
    def _citation_id(source: SourceRef, generation: int, start: int, end: int) -> str:
        identity = "\x1f".join(
            [
                source.tenant_id,
                source.source_type,
                source.source_id,
                source.source_version,
                str(generation),
                str(start),
                str(end),
            ]
        )
        return f"rc-{hashlib.sha256(identity.encode()).hexdigest()}"
