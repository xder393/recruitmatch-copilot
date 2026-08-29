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
    chunks = chunk_document("Python FastAPI recruiting", chunk_size=10, overlap=2)

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
            chunk_document("one two three", chunk_size=5, overlap=1),
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


def test_index_failure_classification_is_stable_and_not_all_embedding_failed():
    from app.retrieval.generations import GenerationConflictError, GenerationValidationError, IndexFailureCode
    from app.retrieval.indexing import classify_index_failure

    assert classify_index_failure(GenerationConflictError("race")) is IndexFailureCode.ACTIVATION_FAILED
    assert classify_index_failure(GenerationValidationError("invalid")) is IndexFailureCode.VALIDATION_FAILED
    assert classify_index_failure(RuntimeError("provider down")) is IndexFailureCode.EMBEDDING_FAILED


def test_fake_writer_shares_retry_and_invalid_generation_contract(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.domain.enums import ResumeStatus
    from app.models.identity import Tenant
    from app.models.resumes import Resume
    from app.retrieval.generations import GenerationValidationError, StagedChunk
    from tests.fakes.retrieval import FakeGenerationWriter, FakeRecruitingVectorIndex

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'generation.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        session.add(Tenant(id="tenant-a", name="Tenant A"))
        session.add(
            Resume(
                id="resume-1",
                tenant_id="tenant-a",
                sha256="sha",
                original_filename="resume.txt",
                media_type="text/plain",
                size_bytes=3,
                status=ResumeStatus.SUCCEEDED,
                profile={},
            )
        )
        session.commit()
    calls = 0

    def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient activation")

    index = FakeRecruitingVectorIndex(session_factory=factory)
    writer = FakeGenerationWriter(factory, index, activation_checkpoint=fail_once)
    source = SourceRef("tenant-a", "resume", "resume-1", "sha")
    vector = [1.0] + [0.0] * 511
    chunks = [StagedChunk("citation", "RAG", 0, 3, vector, "fake-512-v1")]

    assert writer.stage(source, 1, chunks) == 1
    with pytest.raises(RuntimeError, match="transient activation"):
        writer.activate(source, 1, expected_count=1, embedding_model="fake-512-v1")
    assert writer.stage(source, 1, chunks) == 1
    writer.activate(source, 1, expected_count=1, embedding_model="fake-512-v1")
    assert [item.citation_id for item in index._chunks] == ["citation"]

    with pytest.raises(GenerationValidationError, match="positive integer"):
        writer.stage(source, 0, chunks)
