"""Tenant-scoped idempotent repair of resume and job search sources."""

from __future__ import annotations

from app.core.exceptions import AuthorizationError
from app.domain.enums import Role, ResumeStatus
from app.domain.artifacts import ArtifactStatus
from datetime import datetime, timezone
from app.knowledge.chunking import chunk_document
from app.repositories.ports import JobRepository, ResumeRepository
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.retrieval.generations import SourceRef
from app.services.resume_processing import ResumeProcessingService
from app.processing.outcomes import ProcessDisposition
from app.retrieval.indexing import SourceIndexer, classify_index_failure


class SourceIndexBackfillService:
    def __init__(
        self,
        resumes: ResumeRepository,
        source_indexer: SourceIndexer,
        jobs: JobRepository,
        *,
        uow: RecruitingUnitOfWork,
        resume_processor: ResumeProcessingService,
    ):
        self.resumes = resumes
        self.jobs = jobs
        self.uow = uow
        self.source_indexer = source_indexer
        self.resume_processor = resume_processor

    def rebuild(self, principal):
        if principal.role not in {Role.ADMIN, Role.RECRUITER}:
            raise AuthorizationError("无权重建检索索引")
        resumes = self.resumes.list_succeeded(principal.tenant_id)
        versions = self.jobs.list_versions_for_tenant(principal.tenant_id)
        result = {"resumes_indexed": 0, "job_versions_indexed": 0, "failed": 0}
        for resume in resumes:
            current = self.resumes.get(principal.tenant_id, resume.id, for_update=True)
            artifact = (
                self.uow.artifacts.get(principal.tenant_id, "resume", resume.id, current.artifact_id, for_update=True)
                if current is not None and current.artifact_id
                else None
            )
            if (
                current is None
                or current.status != ResumeStatus.SUCCEEDED
                or artifact is None
                or artifact.status != ArtifactStatus.AVAILABLE
            ):
                self.uow.rollback()
                result["failed"] += 1
                continue
            current.status = ResumeStatus.QUEUED
            current.processing_attempts = 0
            current.processing_lease_owner = current.processing_lease_expires_at = None
            current.next_retry_at = current.error_code = current.error_message = None
            current.queued_at = datetime.now(timezone.utc)
            self.uow.commit()
            outcome = self.resume_processor.process(principal.tenant_id, resume.id)
            current = self.resumes.reload(principal.tenant_id, resume.id)
            if (
                outcome == ProcessDisposition.COMPLETED
                and current is not None
                and current.search_index_status == "ready"
                and current.search_index_error_code is None
            ):
                result["resumes_indexed"] += 1
            else:
                result["failed"] += 1
        for version in versions:
            if self._index_job(version, principal.tenant_id):
                result["job_versions_indexed"] += 1
            else:
                result["failed"] += 1
        return result

    def _index_job(self, record, tenant_id):
        source = SourceRef(tenant_id, "job_version", record.id, str(record.version))
        try:
            self.source_indexer.index(
                source,
                record.active_index_generation + 1,
                chunk_document(record.jd_text),
            )
        except Exception as exc:
            try:
                self.source_indexer.fail(source, classify_index_failure(exc))
            except Exception:
                pass
            self.jobs.reload(tenant_id, record.job_id)
            return False
        self.jobs.reload(tenant_id, record.job_id)
        return True
