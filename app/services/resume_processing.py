"""Resume extraction and profiling lifecycle orchestration."""

from __future__ import annotations

from typing import Protocol
from datetime import datetime, timedelta, timezone
import hashlib

from app.core.exceptions import AppError
from app.domain.enums import ResumeStatus
from app.domain.artifacts import ArtifactStatus
from app.artifacts.ports import ArtifactStore, ArtifactChecksumMismatch, ArtifactLengthMismatch, MAX_ARTIFACT_BYTES
from app.artifacts.errors import storage_error_code
from app.resumes.extractors import extract_text
from app.resumes.schemas import ResumeProfile
from app.repositories.unit_of_work import UnitOfWorkFactory
from app.retrieval.generations import SourceRef
from app.retrieval.indexing import SourceIndexer, classify_index_failure


class ResumeParser(Protocol):
    def parse(self, text: str) -> ResumeProfile: ...


class ResumeProcessingService:
    lease_seconds = 300

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        artifact_store: ArtifactStore,
        parser: ResumeParser,
        source_indexer: SourceIndexer | None = None,
    ):
        self.uow_factory = uow_factory
        self.artifact_store = artifact_store
        self.parser = parser
        self.source_indexer = source_indexer

    def process(self, tenant_id: str, resume_id: str) -> bool:
        with self.uow_factory() as uow:
            resume = uow.resumes.get(tenant_id, resume_id, include_deleted=True, for_update=True)
            if (
                resume is None
                or resume.lifecycle_status != "active"
                or resume.status in {ResumeStatus.SUCCEEDED, ResumeStatus.FAILED}
            ):
                return True
            if resume.status is ResumeStatus.RUNNING and not self._lease_expired(resume.updated_at):
                return False
            if resume.status not in {ResumeStatus.QUEUED, ResumeStatus.RUNNING}:
                return True
            artifact = uow.artifacts.get(tenant_id, "resume", resume_id, resume.artifact_id, for_update=True)
            if artifact is None or artifact.status != ArtifactStatus.AVAILABLE:
                return True
            resume.status = ResumeStatus.RUNNING
            resume.updated_at = datetime.now(timezone.utc)
            resume.error_code = None
            resume.error_message = None
            filename = resume.original_filename
            location = uow.artifacts.resolve_location(tenant_id, "resume", resume_id, artifact.id)
            expected_size, expected_sha = artifact.size_bytes, artifact.sha256
            source_version = resume.sha256
            next_generation = resume.active_index_generation + 1
            uow.commit()

        try:
            content = self.artifact_store.read_bounded(location, MAX_ARTIFACT_BYTES)
            if len(content) != expected_size:
                raise ArtifactLengthMismatch()
            if hashlib.sha256(content).hexdigest() != expected_sha:
                raise ArtifactChecksumMismatch()
        except Exception as exc:
            self._mark_failed(tenant_id, resume_id, storage_error_code(exc).value, "无法读取简历文件")
            return True

        try:
            text = extract_text(filename, content)
            parse_result = (
                self.parser.parse_with_metadata(text) if hasattr(self.parser, "parse_with_metadata") else None
            )
            profile = parse_result.profile if parse_result is not None else self.parser.parse(text)
        except AppError as exc:
            self._mark_failed(tenant_id, resume_id, exc.code, exc.message)
            return True
        except Exception:
            self._mark_failed(tenant_id, resume_id, "resume_processing_failed", "简历处理失败")
            return True

        with self.uow_factory() as uow:
            resume = uow.resumes.get(tenant_id, resume_id, include_deleted=True, for_update=True)
            if resume is None or resume.lifecycle_status != "active":
                return True
            artifact = uow.artifacts.get(tenant_id, "resume", resume_id, location.artifact_id, for_update=True)
            if artifact is None or artifact.status != ArtifactStatus.AVAILABLE:
                return True
            resume.extracted_text = text
            resume.profile = profile.model_dump(mode="json")
            resume.status = ResumeStatus.SUCCEEDED
            resume.error_code = None
            resume.error_message = None
            if parse_result is not None:
                if parse_result.response is not None:
                    uow.model_traces.succeeded(
                        tenant_id,
                        "resume_extract",
                        resume_id,
                        [resume_id],
                        parse_result.request,
                        parse_result.response,
                    )
                elif parse_result.error is not None:
                    uow.model_traces.failed(
                        tenant_id,
                        "resume_extract",
                        resume_id,
                        [resume_id],
                        parse_result.request,
                        parse_result.error,
                        parse_result.latency_ms,
                    )
            uow.commit()

        if self.source_indexer is not None:
            source = SourceRef(tenant_id, "resume", resume_id, source_version)
            try:
                from app.knowledge.chunking import chunk_document

                self.source_indexer.index(
                    source, next_generation, chunk_document(text), document_id=location.artifact_id
                )
            except Exception as exc:
                # Search enrichment must never roll back an otherwise valid
                # resume; re-indexing can repair this side effect later.
                try:
                    self.source_indexer.fail(source, classify_index_failure(exc))
                except Exception:
                    pass
        return True

    def _lease_expired(self, updated_at: datetime) -> bool:
        timestamp = updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=timezone.utc)
        return timestamp <= datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds)

    def _mark_failed(self, tenant_id: str, resume_id: str, code: str, message: str) -> None:
        with self.uow_factory() as uow:
            resume = uow.resumes.get(tenant_id, resume_id, include_deleted=True, for_update=True)
            if resume is None or resume.lifecycle_status != "active":
                return
            artifact = uow.artifacts.get(tenant_id, "resume", resume_id, resume.artifact_id, for_update=True)
            if artifact is None or artifact.status != ArtifactStatus.AVAILABLE:
                return
            resume.status = ResumeStatus.FAILED
            resume.error_code = code
            resume.error_message = message[:500]
            uow.commit()
