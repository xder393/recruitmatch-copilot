"""Resume upload, idempotency, retrieval, and deletion rules."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Tuple

from app.core.exceptions import AppError, ResourceNotFoundError
from app.domain.enums import ResumeStatus
from app.models.resumes import Resume, ResumeArtifact
from app.repositories.ports import RepositoryConflictError, ResumeRepository
from app.repositories.unit_of_work import UnitOfWork, unit_of_work
from app.resumes.extractors import FilePolicy
from app.security.tokens import Principal
from app.tasks.dispatcher import TaskDispatcher


class ResumeService:
    def __init__(
        self,
        resumes: ResumeRepository,
        artifact_store,
        dispatcher: TaskDispatcher,
        file_policy: FilePolicy | None = None,
        *,
        uow: UnitOfWork | None = None,
    ):
        self.uow: UnitOfWork
        if uow is None:
            legacy_uow = unit_of_work(resumes)
            self.resumes = legacy_uow.resumes
            self.uow = legacy_uow
        else:
            self.resumes = resumes
            self.uow = uow
        self.artifact_store = artifact_store
        self.dispatcher = dispatcher
        self.file_policy = file_policy or FilePolicy()

    def upload(
        self,
        principal: Principal,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> Tuple[Resume, bool]:
        self.file_policy.validate(filename, media_type, content)
        digest = hashlib.sha256(content).hexdigest()
        existing = self.resumes.find_by_hash(principal.tenant_id, digest)
        if existing is not None:
            if existing.status is ResumeStatus.FAILED and existing.error_code == "task_dispatch_failed":
                existing.status = ResumeStatus.QUEUED
                existing.error_code = None
                existing.error_message = None
                self.uow.commit()
                self._dispatch(principal.tenant_id, existing)
                return self._reload(principal, existing.id), False
            return existing, False

        stored = self.artifact_store.put(principal.tenant_id, filename, content)
        resume = Resume(
            tenant_id=principal.tenant_id,
            uploaded_by=principal.user_id,
            sha256=digest,
            original_filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            status=ResumeStatus.QUEUED,
            artifact=ResumeArtifact(storage_key=stored.key),
        )
        self.resumes.add(resume)
        try:
            self.uow.commit()
        except RepositoryConflictError:
            self.uow.rollback()
            self.artifact_store.delete(stored.key)
            concurrent = self.resumes.find_by_hash(principal.tenant_id, digest)
            if concurrent is None:
                raise
            return concurrent, False

        self._dispatch(principal.tenant_id, resume)
        return self._reload(principal, resume.id), True

    def _dispatch(self, tenant_id: str, resume: Resume) -> None:
        try:
            self.dispatcher.dispatch_resume(tenant_id, resume.id)
        except Exception:
            resume.status = ResumeStatus.FAILED
            resume.error_code = "task_dispatch_failed"
            resume.error_message = "简历处理任务投递失败，重试上传可重新投递"
            self.uow.commit()

    def get(self, principal: Principal, resume_id: str) -> Resume:
        resume = self.resumes.get(principal.tenant_id, resume_id)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        return resume

    def list(self, principal: Principal) -> List[Resume]:
        return self.resumes.list(principal.tenant_id)

    def delete(self, principal: Principal, resume_id: str) -> None:
        resume = self.resumes.get(principal.tenant_id, resume_id, include_deleted=True)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        storage_key = resume.artifact.storage_key if resume.artifact else None
        if resume.status is not ResumeStatus.DELETED:
            self.resumes.scrub_private_data(principal.tenant_id, resume, datetime.now(timezone.utc))
            self.uow.commit()
        if storage_key:
            try:
                self.artifact_store.delete(storage_key)
            except Exception as exc:
                resume.error_code = "artifact_delete_failed"
                resume.error_message = "原始简历文件清理失败，可重试删除"
                self.uow.commit()
                raise AppError(
                    "原始简历文件清理待重试",
                    code="artifact_cleanup_pending",
                    status_code=503,
                ) from exc
        resume.error_code = None
        resume.error_message = None
        self.uow.commit()

    def _reload(self, principal: Principal, resume_id: str) -> Resume:
        resume = self.resumes.reload(principal.tenant_id, resume_id)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        return resume
