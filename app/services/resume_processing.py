"""Resume extraction and fenced profile/index publication."""

from __future__ import annotations

from typing import Protocol
from datetime import timedelta
from uuid import uuid4
import hashlib
import time
from app.observability.events import observed, operation, record

from app.core.exceptions import AppError
from app.artifacts.ports import ArtifactStore, ArtifactChecksumMismatch, ArtifactLengthMismatch, MAX_ARTIFACT_BYTES
from app.artifacts.errors import storage_error_code
from app.resumes.extractors import extract_text
from app.resumes.schemas import ResumeProfile, Evidence
from app.repositories.unit_of_work import UnitOfWorkFactory
from app.retrieval.generations import SourceRef, GenerationWriterError
from app.retrieval.indexing import SourceIndexer, classify_index_failure, EmbeddingFailure
from app.processing.outcomes import ClaimDisposition, ClaimedLease, LeaseOwnershipLost, ProcessDisposition
from app.processing.renewal import LeaseRenewer
from app.processing.retry import RetryPolicy, finish_failed_attempt
from app.ai.evidence import evidence_resolves


class ResumeParser(Protocol):
    def parse(self, text: str) -> ResumeProfile: ...


class ResumeProcessingService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        artifact_store: ArtifactStore,
        parser: ResumeParser,
        source_indexer: SourceIndexer | None = None,
        *,
        lease_seconds: int = 300,
        retry_policy: RetryPolicy | None = None,
        timeout_errors: tuple[type[Exception], ...] = (TimeoutError,),
    ):
        self.uow_factory = uow_factory
        self.artifact_store = artifact_store
        self.parser = parser
        self.source_indexer = source_indexer
        self.lease_seconds = lease_seconds
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_errors = timeout_errors

    @observed("resume.process", {"task.type": "resume"})
    def process(self, tenant_id: str, resume_id: str) -> ProcessDisposition:
        duration = timedelta(seconds=self.lease_seconds)
        with operation("lease.claim", {"source.type": "resume"}), self.uow_factory() as uow:
            claim = uow.leases.claim(
                tenant_id,
                "resume",
                resume_id,
                uuid4().hex,
                duration=duration,
                max_attempts=self.retry_policy.max_attempts,
            )
            if claim.disposition != ClaimDisposition.CLAIMED:
                uow.commit()
                return ProcessDisposition(claim.disposition.value)
            lease = claim.lease
            assert lease is not None
            resume = uow.resumes.get(tenant_id, resume_id)
            artifact = uow.artifacts.get(tenant_id, "resume", resume_id, lease.artifact_id)
            assert resume is not None and artifact is not None  # Exact rows locked by claim.
            filename = resume.original_filename
            location = uow.artifacts.resolve_location(tenant_id, "resume", resume_id, lease.artifact_id)
            expected_size, expected_sha = artifact.size_bytes, artifact.sha256
            source = SourceRef(tenant_id, "resume", resume_id, resume.sha256)
            generation = resume.active_index_generation + 1
            stored_text = resume.extracted_text
            stored_profile = resume.profile
            uow.commit()
        record("task.started", {"task.type": "resume", "outcome": "claimed"})
        if claim.takeover:
            record("lease.takeover", {"source.type": "resume", "recovery.reason": "lease_expired"})
        started = time.perf_counter()
        try:
            with LeaseRenewer(self.uow_factory, lease, duration=duration) as renewer:
                try:
                    already_parsed = self._has_parsed_state(stored_text, stored_profile)
                except ValueError:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, "resume_parsed_state_invalid")
                if already_parsed:
                    self._index_and_publish(lease, source, generation, stored_text, None, None, renewer)
                    return ProcessDisposition.COMPLETED
                try:
                    content = self.artifact_store.read_bounded(location, MAX_ARTIFACT_BYTES)
                    if len(content) != expected_size:
                        raise ArtifactLengthMismatch()
                    if hashlib.sha256(content).hexdigest() != expected_sha:
                        raise ArtifactChecksumMismatch()
                except self.timeout_errors:
                    raise
                except Exception as exc:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, storage_error_code(exc).value)
                renewer.ensure_owned()
                try:
                    with operation("resume.parse", {"source.type": "resume"}):
                        text = extract_text(filename, content)
                        parse_result = (
                            self.parser.parse_with_metadata(text)
                            if hasattr(self.parser, "parse_with_metadata")
                            else None
                        )
                        profile = parse_result.profile if parse_result is not None else self.parser.parse(text)
                except AppError as exc:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, exc.code)
                except self.timeout_errors:
                    raise
                except Exception:
                    renewer.ensure_owned()
                    return self._mark_failed(lease, "resume_processing_failed")
                renewer.ensure_owned()
                self._index_and_publish(lease, source, generation, text, profile, parse_result, renewer)
            return ProcessDisposition.COMPLETED
        except LeaseOwnershipLost:
            return ProcessDisposition.LEASE_LOST
        except self.timeout_errors:
            return self._mark_failed(lease, "processing_timeout")
        finally:
            record("task.duration", {"task.type": "resume"}, time.perf_counter() - started)

    @staticmethod
    def _has_parsed_state(text, profile) -> bool:
        # Only successful atomic publication writes this pair. The immutable
        # Artifact means ordinary recovery can infer index-only work durably.
        if text is None and profile == {}:
            return False
        if not isinstance(text, str) or not text.strip() or not isinstance(profile, dict):
            raise ValueError("resume_parsed_state_invalid")
        if not ResumeProfile.model_fields.keys() <= profile.keys():
            raise ValueError("resume_parsed_state_invalid")
        parsed = ResumeProfile.model_validate(profile)
        evidence: list[Evidence | None] = []
        evidence.extend(item.evidence for item in parsed.skills)
        evidence.extend(item.evidence for item in parsed.projects)
        if parsed.experience_years is not None:
            evidence.append(parsed.experience_evidence)
        if parsed.education_level is not None:
            evidence.append(parsed.education_evidence)
        if parsed.schema_version != "1.0" or any(not evidence_resolves(text, item) for item in evidence):
            raise ValueError("resume_parsed_state_invalid")
        return True

    def _index_and_publish(self, lease, source, generation, text, profile, parse_result, renewer):
        count, index_error = None, None
        if self.source_indexer is not None:
            from app.knowledge.chunking import chunk_document

            try:
                renewer.ensure_owned()
                self.source_indexer.reconcile_staging(source, generation, fencing_token=lease)
                if not text:
                    raise ValueError("source_text_missing")
                count = self.source_indexer.stage(
                    source, generation, chunk_document(text), document_id=lease.artifact_id, fencing_token=lease
                )
            except LeaseOwnershipLost:
                raise
            except (GenerationWriterError, EmbeddingFailure, ValueError) as exc:
                if isinstance(exc.__cause__, self.timeout_errors):
                    raise exc.__cause__ from None
                index_error = classify_index_failure(exc)
        renewer.ensure_owned()
        try:
            self._publish(lease, source, generation, count, index_error, text, profile, parse_result)
        except LeaseOwnershipLost:
            raise
        except GenerationWriterError as exc:
            # Failed activation rolls back publication. A fresh guarded transaction
            # can still publish deterministic parsing, retaining any prior index.
            if count is None:
                raise
            renewer.ensure_owned()
            self._publish(lease, source, generation, None, classify_index_failure(exc), text, profile, parse_result)

    def _publish(self, lease, source, generation, count, index_error, text, profile, parse_result):
        with operation("lease.finalize", {"source.type": "resume"}), self.uow_factory() as uow:
            with uow.leases.finalize_owned(lease):
                resume = uow.resumes.get(lease.tenant_id, lease.source_id)
                if resume is None or resume.sha256 != source.source_version:
                    raise LeaseOwnershipLost("processing_source_version_changed")
                if count is not None:
                    assert self.source_indexer is not None
                    uow.generations.activate(
                        source,
                        generation,
                        expected_count=count,
                        embedding_model=self.source_indexer.embedder.model_name,
                        fencing_token=lease,
                    )
                elif index_error is not None:
                    uow.generations.fail(source, index_error, fencing_token=lease)
                if profile is not None:
                    resume.extracted_text = text
                    resume.profile = profile.model_dump(mode="json")
                if parse_result is not None:
                    if parse_result.response is not None:
                        uow.model_traces.succeeded(
                            lease.tenant_id,
                            "resume_extract",
                            lease.source_id,
                            [lease.source_id],
                            parse_result.request,
                            parse_result.response,
                        )
                    elif parse_result.error is not None:
                        uow.model_traces.failed(
                            lease.tenant_id,
                            "resume_extract",
                            lease.source_id,
                            [lease.source_id],
                            parse_result.request,
                            parse_result.error,
                            parse_result.latency_ms,
                        )
            uow.commit()
        record("task.completed", {"task.type": "resume", "outcome": "success"})
        if count is not None:
            record("vector.indexed_chunks", {"source.type": "resume", "outcome": "success"}, count)
        if index_error is not None:
            record(
                "model.fallback", {"operation": "vector.index", "outcome": "fallback", "error.code": index_error.value}
            )

    def _mark_failed(self, lease: ClaimedLease, code: str) -> ProcessDisposition:
        with self.uow_factory() as uow:
            return finish_failed_attempt(uow, lease, code, self.retry_policy)
