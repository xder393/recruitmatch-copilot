"""Complete-generation indexing orchestration shared by API and workers."""

from __future__ import annotations

import hashlib
from typing import Protocol

from app.knowledge.schemas import ChunkInput
from app.retrieval.generations import IndexFailureCode, SourceRef, StagedChunk


class EmbeddingAdapter(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class GenerationWriterPort(Protocol):
    def stage(self, source: SourceRef, generation: int, chunks: list[StagedChunk], *, fencing_token=None) -> int: ...

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
        fencing_token: int | None = None,
    ) -> int:
        if not chunks:
            raise ValueError("at least one source chunk is required")
        vectors = self.embedder.embed_documents([chunk.content for chunk in chunks])
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
        count = self.writer.stage(source, generation, staged, fencing_token=fencing_token)
        self.writer.activate(
            source,
            generation,
            expected_count=count,
            embedding_model=self.embedder.model_name,
            fencing_token=fencing_token,
        )
        return count

    def fail(
        self,
        source: SourceRef,
        error_code: IndexFailureCode,
        *,
        fencing_token: int | None = None,
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
