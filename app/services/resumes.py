"""Resume upload, idempotency, retrieval, and deletion rules."""

from __future__ import annotations

from uuid import uuid4
from datetime import datetime, timezone
from typing import BinaryIO, List, Tuple

from app.core.exceptions import AppError, ResourceNotFoundError
from app.domain.enums import ResumeStatus
from app.domain.artifacts import ArtifactErrorCode, ArtifactStatus
from app.artifacts.cleanup import cleanup_location
from app.artifacts.ports import ArtifactStore
from app.artifacts.errors import storage_error_code
from app.models.resumes import Resume
from app.repositories.ports import ResumeRepository
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.resumes.extractors import FilePolicy, PreparedUpload
from app.security.tokens import Principal
from app.tasks.dispatcher import TaskDispatcher


class ResumeService:
    def __init__(
        self,
        resumes: ResumeRepository,
        artifact_store: ArtifactStore,
        dispatcher: TaskDispatcher,
        file_policy: FilePolicy | None = None,
        *,
        uow: RecruitingUnitOfWork,
    ):
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
        content: bytes | BinaryIO,
    ) -> Tuple[Resume, bool]:
        with self.file_policy.prepare(filename, media_type, content) as prepared:
            return self._upload(principal, filename, media_type, prepared)

    def _upload(self, principal: Principal, filename: str, media_type: str, prepared: PreparedUpload):
        digest, size = prepared.sha256, prepared.size_bytes
        proposed_id = str(uuid4())
        artifact = self.uow.artifacts.claim_upload(principal.tenant_id, "resume", proposed_id, digest, media_type, size)
        if artifact.owner_id != proposed_id:
            existing = self.resumes.get(principal.tenant_id, artifact.owner_id)
            self.uow.commit()
            if existing is None:
                raise ResourceNotFoundError("简历不存在")
            return existing, False

        resume: Resume | None = Resume(
            id=artifact.owner_id,
            tenant_id=principal.tenant_id,
            uploaded_by=principal.user_id,
            sha256=digest,
            original_filename=filename,
            media_type=media_type,
            size_bytes=size,
            status=ResumeStatus.QUEUED,
            artifact_id=artifact.id,
        )
        self.resumes.add(resume)
        location = self.uow.artifacts.resolve_location(principal.tenant_id, "resume", artifact.owner_id, artifact.id)
        self.uow.commit()
        try:
            self.artifact_store.put(location, prepared.stream, size, digest)
        except Exception as exc:
            code = storage_error_code(exc)
            resume = self.resumes.get(principal.tenant_id, location.owner_id, include_deleted=True, for_update=True)
            current = self.uow.artifacts.get(
                principal.tenant_id, "resume", location.owner_id, location.artifact_id, for_update=True
            )
            if (
                resume is not None
                and resume.lifecycle_status == "active"
                and current is not None
                and current.status == ArtifactStatus.PENDING
            ):
                self.uow.artifacts.mark_failed(
                    principal.tenant_id, location.artifact_id, code, owner_type="resume", owner_id=location.owner_id
                )
                resume.status = ResumeStatus.FAILED
                resume.error_code = code.value
                resume.error_message = "简历文件上传失败"
                self.uow.commit()
            else:
                self.uow.rollback()
                if current is not None and current.status in {
                    ArtifactStatus.CLEANUP_PENDING,
                    ArtifactStatus.CLEANUP_FAILED,
                    ArtifactStatus.DELETED,
                }:
                    cleanup_location(self.uow, self.artifact_store, location, "resume")
            raise AppError("简历文件上传失败", code=code.value, status_code=503) from exc
        resume = self.resumes.get(principal.tenant_id, location.owner_id, include_deleted=True, for_update=True)
        current = self.uow.artifacts.get(
            principal.tenant_id, "resume", location.owner_id, location.artifact_id, for_update=True
        )
        if (
            resume is None
            or resume.lifecycle_status == "deleted"
            or current is None
            or current.status
            not in {
                ArtifactStatus.PENDING,
                ArtifactStatus.AVAILABLE,
            }
        ):
            self.uow.rollback()
            cleanup_location(self.uow, self.artifact_store, location, "resume")
            raise ResourceNotFoundError("简历不存在")
        self.uow.artifacts.mark_available(
            principal.tenant_id, location.artifact_id, owner_type="resume", owner_id=location.owner_id
        )
        self.uow.commit()
        self._dispatch(principal.tenant_id, resume)
        return self._reload(principal, resume.id), True

    def _dispatch(self, tenant_id: str, resume: Resume) -> None:
        try:
            self.dispatcher.dispatch_resume(tenant_id, resume.id)
        except Exception:
            current = self.resumes.get(tenant_id, resume.id, include_deleted=True, for_update=True)
            if current is None or current.lifecycle_status != "active" or current.status != ResumeStatus.QUEUED:
                self.uow.rollback()
                return
            current.error_code = "task_dispatch_failed"
            current.error_message = "简历处理任务等待恢复投递"
            self.uow.commit()

    def get(self, principal: Principal, resume_id: str) -> Resume:
        resume = self.resumes.get(principal.tenant_id, resume_id)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        return resume

    def list(self, principal: Principal) -> List[Resume]:
        return self.resumes.list(principal.tenant_id)

    def delete(self, principal: Principal, resume_id: str) -> None:
        self.uow.identities.lock_privacy_guard(principal.tenant_id)
        resume = self.resumes.get(principal.tenant_id, resume_id, include_deleted=True, for_update=True)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        location = None
        if resume.artifact_id is not None:
            artifact = self.uow.artifacts.get(
                principal.tenant_id, "resume", resume.id, resume.artifact_id, for_update=True
            )
            if artifact is not None:
                location = self.uow.artifacts.resolve_location(principal.tenant_id, "resume", resume.id, artifact.id)
                scope = {"owner_type": "resume", "owner_id": resume.id}
                if artifact.status == ArtifactStatus.PENDING:
                    self.uow.artifacts.mark_failed(
                        principal.tenant_id, artifact.id, ArtifactErrorCode.STORAGE_UNAVAILABLE, **scope
                    )
                if artifact.status in {ArtifactStatus.AVAILABLE, ArtifactStatus.FAILED, ArtifactStatus.CLEANUP_FAILED}:
                    self.uow.artifacts.mark_cleanup_pending(principal.tenant_id, artifact.id, **scope)
        if resume.lifecycle_status != "deleted":
            self.resumes.scrub_private_data(principal.tenant_id, resume, datetime.now(timezone.utc))
        self.uow.commit()
        if location is not None:
            cleanup_location(self.uow, self.artifact_store, location, "resume")

    def _reload(self, principal: Principal, resume_id: str) -> Resume:
        resume = self.resumes.reload(principal.tenant_id, resume_id)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        return resume
