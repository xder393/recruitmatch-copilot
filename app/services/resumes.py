"""Resume upload, idempotency, retrieval, and deletion rules."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceNotFoundError
from app.domain.enums import ResumeStatus
from app.models.resumes import Resume, ResumeArtifact
from app.models.knowledge import KnowledgeChunk
from app.models.matching import MatchResult, MatchRun
from app.repositories.resumes import ResumeRepository
from app.resumes.extractors import FilePolicy
from app.security.tokens import Principal


class ResumeService:
    def __init__(self, session: Session, artifact_store, dispatcher, file_policy: FilePolicy | None = None):
        self.session = session
        self.artifact_store = artifact_store
        self.dispatcher = dispatcher
        self.file_policy = file_policy or FilePolicy()
        self.resumes = ResumeRepository(session)

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
                self.session.commit()
                self._dispatch(principal.tenant_id, existing)
                self.session.expire_all()
                return self.get(principal, existing.id), False
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
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            self.artifact_store.delete(stored.key)
            concurrent = self.resumes.find_by_hash(principal.tenant_id, digest)
            if concurrent is None:
                raise
            return concurrent, False

        self._dispatch(principal.tenant_id, resume)
        self.session.expire_all()
        return self.get(principal, resume.id), True

    def _dispatch(self, tenant_id: str, resume: Resume) -> None:
        try:
            self.dispatcher.dispatch_resume(tenant_id, resume.id)
        except Exception:
            resume.status = ResumeStatus.FAILED
            resume.error_code = "task_dispatch_failed"
            resume.error_message = "简历处理任务投递失败，重试上传可重新投递"
            self.session.commit()

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
            resume.sha256 = hashlib.sha256(f"deleted:{resume.id}".encode()).hexdigest()
            resume.original_filename = "deleted"
            resume.size_bytes = 0
            resume.uploaded_by = None
            resume.profile = {}
            resume.error_code = None
            resume.error_message = None
            resume.search_index_status = "deleted"
            resume.search_index_error = None
            resume.search_indexed_at = None
            if resume.artifact is not None:
                resume.artifact.extracted_text = None
            self.session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.tenant_id == principal.tenant_id,
                    KnowledgeChunk.source_type == "resume",
                    KnowledgeChunk.source_id == resume.id,
                )
            )
            run_ids = select(MatchRun.id).where(
                MatchRun.tenant_id == principal.tenant_id,
                MatchRun.resume_id == resume.id,
            )
            self.session.execute(
                update(MatchResult)
                .where(MatchResult.run_id.in_(run_ids))
                .values(
                    dimension_scores={},
                    matched_items=[],
                    missing_items=[],
                    uncertain_items=[],
                    evidence=[],
                    risk_flags=[],
                    summary=None,
                    citations=[],
                    grounded_explanation={},
                    interview_questions=[],
                )
            )
            resume.status = ResumeStatus.DELETED
            resume.deleted_at = datetime.now(timezone.utc)
            self.session.commit()
        if storage_key:
            try:
                self.artifact_store.delete(storage_key)
            except Exception as exc:
                resume.error_code = "artifact_delete_failed"
                resume.error_message = "原始简历文件清理失败，可重试删除"
                self.session.commit()
                raise AppError(
                    "原始简历文件清理待重试",
                    code="artifact_cleanup_pending",
                    status_code=503,
                ) from exc
        resume.error_code = None
        resume.error_message = None
        self.session.commit()
