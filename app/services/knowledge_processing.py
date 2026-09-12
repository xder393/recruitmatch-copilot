"""Generation-safe recruiting knowledge processing."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4
import hashlib

from app.core.exceptions import AppError
from app.artifacts.ports import ArtifactStore, ArtifactChecksumMismatch, ArtifactLengthMismatch, MAX_ARTIFACT_BYTES
from app.artifacts.errors import storage_error_code
from app.knowledge.chunking import chunk_document
from app.repositories.unit_of_work import UnitOfWorkFactory
from app.resumes.extractors import extract_text
from app.retrieval.generations import SourceRef, GenerationWriterError
from app.retrieval.indexing import SourceIndexer, classify_index_failure, EmbeddingFailure
from app.processing.outcomes import ClaimDisposition, ClaimedLease, LeaseOwnershipLost, ProcessDisposition
from app.processing.renewal import LeaseRenewer
from app.processing.retry import RetryPolicy, TRANSIENT_PROCESSING_CODES


class KnowledgeProcessingService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        artifact_store: ArtifactStore,
        source_indexer: SourceIndexer,
        *,
        lease_seconds: int = 300,
        retry_policy: RetryPolicy | None = None,
        timeout_errors: tuple[type[Exception], ...] = (TimeoutError,),
    ):
        self.uow_factory = uow_factory
        self.artifact_store = artifact_store
        self.source_indexer = source_indexer
        self.lease_seconds = lease_seconds
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_errors = timeout_errors

    def process(self, tenant_id: str, document_id: str) -> ProcessDisposition:
        duration = timedelta(seconds=self.lease_seconds)
        with self.uow_factory() as uow:
            claim = uow.leases.claim(
                tenant_id,
                "knowledge_document",
                document_id,
                uuid4().hex,
                duration=duration,
                max_attempts=self.retry_policy.max_attempts,
            )
            if claim.disposition != ClaimDisposition.CLAIMED:
                uow.commit()
                return ProcessDisposition(claim.disposition.value)
            lease = claim.lease
            assert lease is not None
            document = uow.knowledge.get_document(tenant_id, document_id)
            artifact = uow.artifacts.get(tenant_id, "knowledge_document", document_id, lease.artifact_id)
            assert document is not None and artifact is not None  # Exact rows locked by claim.
            filename = document.original_filename
            location = uow.artifacts.resolve_location(tenant_id, "knowledge_document", document_id, lease.artifact_id)
            expected_size, expected_sha = artifact.size_bytes, artifact.sha256
            source = SourceRef(tenant_id, "knowledge_document", document_id, document.checksum)
            generation = document.active_index_generation + 1
            uow.commit()

        try:
            with LeaseRenewer(self.uow_factory, lease, duration=duration) as renewer:
                try:
                    content = self.artifact_store.read_bounded(location, MAX_ARTIFACT_BYTES)
                    if len(content) != expected_size:
                        raise ArtifactLengthMismatch()
                    if hashlib.sha256(content).hexdigest() != expected_sha:
                        raise ArtifactChecksumMismatch()
                except Exception as exc:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, storage_error_code(exc).value)
                renewer.ensure_owned()
                try:
                    text = extract_text(filename, content)
                    chunks = chunk_document(text)
                except AppError as exc:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, exc.code)
                except self.timeout_errors:
                    raise
                except Exception:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, "knowledge_processing_failed")
                renewer.ensure_owned()
                try:
                    self.source_indexer.reconcile_staging(source, generation, fencing_token=lease)
                    count = self.source_indexer.stage(
                        source, generation, chunks, document_id=document_id, fencing_token=lease
                    )
                    renewer.ensure_owned()
                    with self.uow_factory() as uow:
                        with uow.leases.finalize_owned(lease):
                            uow.generations.activate(
                                source,
                                generation,
                                expected_count=count,
                                embedding_model=self.source_indexer.embedder.model_name,
                                fencing_token=lease,
                            )
                        uow.commit()
                except LeaseOwnershipLost:
                    raise
                except (GenerationWriterError, EmbeddingFailure, ValueError) as exc:
                    if isinstance(exc.__cause__, self.timeout_errors):
                        raise exc.__cause__ from None
                    renewer.ensure_owned()
                    index_error = classify_index_failure(exc)
                    return self._mark_failed(lease, index_error.value, source, index_error)
            return ProcessDisposition.COMPLETED
        except LeaseOwnershipLost:
            return ProcessDisposition.LEASE_LOST
        except self.timeout_errors:
            return self._mark_failed(lease, "processing_timeout")

    def _mark_failed(self, lease: ClaimedLease, code: str, source=None, index_error=None) -> ProcessDisposition:
        with self.uow_factory() as uow:
            if index_error is not None:
                with uow.leases.owned(lease):
                    uow.generations.fail(source, index_error, fencing_token=lease)
            if code in TRANSIENT_PROCESSING_CODES:
                outcome = uow.leases.schedule_retry(
                    lease,
                    code,
                    short_delay=timedelta(seconds=self.retry_policy.short_seconds),
                    long_delay=timedelta(seconds=self.retry_policy.long_seconds),
                    max_attempts=self.retry_policy.max_attempts,
                )
            else:
                outcome = (
                    ProcessDisposition.COMPLETED if uow.leases.fail(lease, code) else ProcessDisposition.LEASE_LOST
                )
            if outcome == ProcessDisposition.LEASE_LOST:
                uow.rollback()
                return outcome
            uow.commit()
        return outcome
