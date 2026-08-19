"""Tenant-scoped idempotent repair of resume and job search sources."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthorizationError
from app.domain.enums import ResumeStatus, Role
from app.knowledge.chunking import chunk_document
from app.models.jobs import Job, JobVersion
from app.models.resumes import Resume


class SourceIndexBackfillService:
    def __init__(self, session, source_index):
        self.session = session
        self.source_index = source_index

    def rebuild(self, principal):
        if principal.role not in {Role.ADMIN, Role.RECRUITER}:
            raise AuthorizationError("无权重建检索索引")
        resumes = list(
            self.session.scalars(
                select(Resume)
                .options(selectinload(Resume.artifact))
                .where(Resume.tenant_id == principal.tenant_id, Resume.status == ResumeStatus.SUCCEEDED)
            )
        )
        versions = list(
            self.session.scalars(
                select(JobVersion).join(Job).where(Job.tenant_id == principal.tenant_id)
            )
        )
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
        self.session.commit()
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
