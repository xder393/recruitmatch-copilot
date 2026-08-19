"""Resume upload, idempotency, retrieval, and deletion rules."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundError
from app.domain.enums import ResumeStatus
from app.models.resumes import Resume, ResumeArtifact
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

        self.dispatcher.dispatch_resume(principal.tenant_id, resume.id)
        return self.get(principal, resume.id), True

    def get(self, principal: Principal, resume_id: str) -> Resume:
        resume = self.resumes.get(principal.tenant_id, resume_id)
        if resume is None:
            raise ResourceNotFoundError("简历不存在")
        return resume

    def list(self, principal: Principal) -> List[Resume]:
        return self.resumes.list(principal.tenant_id)

    def delete(self, principal: Principal, resume_id: str) -> None:
        resume = self.get(principal, resume_id)
        storage_key = resume.artifact.storage_key if resume.artifact else None
        resume.status = ResumeStatus.DELETED
        resume.deleted_at = datetime.now(timezone.utc)
        self.session.commit()
        if storage_key:
            self.artifact_store.delete(storage_key)
