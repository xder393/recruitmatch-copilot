from __future__ import annotations

import math

import pytest

from app.knowledge.chunking import chunk_document
from app.retrieval.generations import SourceRef
from app.retrieval.indexing import SourceIndexer


class RecordingWriter:
    def __init__(self) -> None:
        self.staged = []
        self.activated = []
        self.failed = []

    def stage(self, source, generation, chunks, *, fencing_token=None):
        self.staged.append((source, generation, chunks, fencing_token))
        return len(chunks)

    def activate(self, source, generation, *, expected_count, embedding_model, fencing_token=None):
        self.activated.append((source, generation, expected_count, embedding_model, fencing_token))

    def fail(self, source, error_code, *, fencing_token=None):
        self.failed.append((source, error_code, fencing_token))


class Embedder:
    model_name = "fake-512-v1"

    def embed_documents(self, texts):
        vector = [0.0] * 512
        vector[0] = 1.0
        return [list(vector) for _ in texts]

    def embed_query(self, text):
        vector = [0.0] * 512
        vector[0] = 1.0
        return vector


def test_source_indexer_stages_and_activates_one_complete_canonical_generation():
    """Catches producers writing old source types, unstable citations, or partial generations."""
    writer = RecordingWriter()
    source = SourceRef("tenant-a", "job_version", "version-1", "3")
    chunks = chunk_document("Python FastAPI recruiting", "job_version", chunk_size=10, overlap=2)

    count = SourceIndexer(writer, Embedder()).index(source, 2, chunks)

    assert count == len(chunks)
    staged_source, generation, staged, token = writer.staged[0]
    assert staged_source == source
    assert generation == 2
    assert token is None
    assert [item.content for item in staged] == [item.content for item in chunks]
    assert all(item.embedding_model == "fake-512-v1" for item in staged)
    assert all(math.isclose(sum(value * value for value in item.embedding), 1.0) for item in staged)
    assert len({item.citation_id for item in staged}) == len(staged)
    assert writer.activated == [(source, 2, len(chunks), "fake-512-v1", None)]


def test_source_indexer_rejects_incomplete_embedding_batch_before_staging():
    """Catches activation after an embedder silently drops a chunk."""

    class IncompleteEmbedder(Embedder):
        def embed_documents(self, texts):
            return super().embed_documents(texts)[:-1]

    writer = RecordingWriter()
    source = SourceRef("tenant-a", "resume", "resume-1", "sha256")

    with pytest.raises(ValueError, match="complete vector"):
        SourceIndexer(writer, IncompleteEmbedder()).index(
            source,
            1,
            chunk_document("one two three", "resume", chunk_size=5, overlap=1),
        )

    assert writer.staged == []
    assert writer.activated == []


def test_source_indexer_records_a_stable_refresh_failure():
    """Catches producers leaking exception text or overwriting the old active generation."""
    from app.retrieval.generations import IndexFailureCode

    writer = RecordingWriter()
    source = SourceRef("tenant-a", "resume", "resume-1", "sha256")

    SourceIndexer(writer, Embedder()).fail(source, IndexFailureCode.EMBEDDING_FAILED)

    assert writer.failed == [(source, IndexFailureCode.EMBEDDING_FAILED, None)]
