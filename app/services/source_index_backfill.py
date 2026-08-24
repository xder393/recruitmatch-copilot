"""Tenant-scoped idempotent repair of resume and job search sources."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.exceptions import AuthorizationError
from app.domain.enums import Role
from app.knowledge.chunking import chunk_document
from app.repositories.ports import JobRepository, ResumeRepository
from app.repositories.unit_of_work import UnitOfWork, unit_of_work


class SourceIndexBackfillService:
    def __init__(
        self,
        resumes: ResumeRepository,
        source_index,
        jobs: JobRepository | None = None,
        *,
        uow: UnitOfWork | None = None,
    ):
        self.uow: UnitOfWork
        if uow is None:
            legacy_uow = unit_of_work(resumes)
            self.resumes = legacy_uow.resumes
            self.jobs = legacy_uow.jobs
            self.uow = legacy_uow
        else:
            if jobs is None:
                raise TypeError("jobs repository is required")
            self.resumes = resumes
            self.jobs = jobs
            self.uow = uow
        self.source_index = source_index

    def rebuild(self, principal):
        if principal.role not in {Role.ADMIN, Role.RECRUITER}:
            raise AuthorizationError("无权重建检索索引")
        resumes = self.resumes.list_succeeded(principal.tenant_id)
        versions = self.jobs.list_versions_for_tenant(principal.tenant_id)
        result = {"resumes_indexed": 0, "job_versions_indexed": 0, "failed": 0}
        for resume in resumes:
            text = resume.artifact.extracted_text if resume.artifact else None
            if not text:
                self._failed(resume, "extracted_text_missing")
                result["failed"] += 1
                continue
            if self._index(resume, principal.tenant_id, "resume", resume.id, resume.sha256, text):
                result["resumes_indexed"] += 1
            else:
                result["failed"] += 1
        for version in versions:
            if self._index(version, principal.tenant_id, "job", version.id, str(version.version), version.jd_text):
                result["job_versions_indexed"] += 1
            else:
                result["failed"] += 1
        self.uow.commit()
        return result

    def _index(self, record, tenant_id, source_type, source_id, source_version, text):
        record.search_index_status = "indexing"
        record.search_index_error = None
        try:
            self.source_index.index_source(
                tenant_id,
                source_type,
                source_id,
                source_version,
                chunk_document(text, source_type),
            )
        except Exception:
            self._failed(record, "indexing_failed")
            return False
        record.search_index_status = "ready"
        record.search_index_error = None
        record.search_indexed_at = datetime.now(timezone.utc)
        return True

    @staticmethod
    def _failed(record, code):
        record.search_index_status = "failed"
        record.search_index_error = code
