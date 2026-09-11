"""Tenant-scoped idempotent repair of resume and job search sources."""

from __future__ import annotations

from app.core.exceptions import AuthorizationError
from app.domain.enums import Role
from app.knowledge.chunking import chunk_document
from app.repositories.ports import JobRepository, ResumeRepository
from app.repositories.unit_of_work import UnitOfWork
from app.retrieval.generations import IndexFailureCode, SourceRef
from app.retrieval.indexing import SourceIndexer, classify_index_failure


class SourceIndexBackfillService:
    def __init__(
        self,
        resumes: ResumeRepository,
        source_indexer: SourceIndexer,
        jobs: JobRepository,
        *,
        uow: UnitOfWork,
    ):
        self.resumes = resumes
        self.jobs = jobs
        self.uow = uow
        self.source_indexer = source_indexer

    def rebuild(self, principal):
        if principal.role not in {Role.ADMIN, Role.RECRUITER}:
            raise AuthorizationError("无权重建检索索引")
        resumes = self.resumes.list_succeeded(principal.tenant_id)
        versions = self.jobs.list_versions_for_tenant(principal.tenant_id)
        result = {"resumes_indexed": 0, "job_versions_indexed": 0, "failed": 0}
        for resume in resumes:
            text = resume.extracted_text
            if not text:
                source = SourceRef(principal.tenant_id, "resume", resume.id, resume.sha256)
                self.source_indexer.fail(source, IndexFailureCode.VALIDATION_FAILED)
                self.resumes.reload(principal.tenant_id, resume.id)
                result["failed"] += 1
                continue
            if self._index(resume, principal.tenant_id, "resume", resume.id, resume.sha256, text):
                result["resumes_indexed"] += 1
            else:
                result["failed"] += 1
        for version in versions:
            if self._index(
                version,
                principal.tenant_id,
                "job_version",
                version.id,
                str(version.version),
                version.jd_text,
            ):
                result["job_versions_indexed"] += 1
            else:
                result["failed"] += 1
        return result

    def _index(self, record, tenant_id, source_type, source_id, source_version, text):
        source = SourceRef(tenant_id, source_type, source_id, source_version)
        try:
            self.source_indexer.index(
                source,
                record.active_index_generation + 1,
                chunk_document(text),
                document_id=record.artifact_id if source_type == "resume" else None,
            )
        except Exception as exc:
            try:
                self.source_indexer.fail(source, classify_index_failure(exc))
            except Exception:
                pass
            self._reload(tenant_id, source_type, record)
            return False
        self._reload(tenant_id, source_type, record)
        return True

    def _reload(self, tenant_id, source_type, record):
        if source_type == "resume":
            self.resumes.reload(tenant_id, record.id)
        else:
            self.jobs.reload(tenant_id, record.job_id)
