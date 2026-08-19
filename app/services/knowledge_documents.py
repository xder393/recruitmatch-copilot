"""Recruiting knowledge upload, idempotency, and lifecycle rules."""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundError, UnsupportedFileError
from app.models.knowledge import KnowledgeDocument
from app.repositories.knowledge import KnowledgeRepository
from app.resumes.extractors import FilePolicy
from app.security.tokens import Principal

_DOCUMENT_TYPES = {"policy", "interview_guide", "competency", "assessment_rubric"}


class KnowledgeDocumentService:
    def __init__(self, session: Session, artifact_store, dispatcher, file_policy: FilePolicy | None = None):
        self.session = session
        self.artifact_store = artifact_store
        self.dispatcher = dispatcher
        self.file_policy = file_policy or FilePolicy()
        self.repository = KnowledgeRepository(session)

    def upload(
        self,
        principal: Principal,
        document_type: str,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> tuple[KnowledgeDocument, bool]:
        if document_type not in _DOCUMENT_TYPES:
            raise UnsupportedFileError("不支持的招聘知识文档类型")
        self.file_policy.validate(filename, media_type, content)
        checksum = hashlib.sha256(content).hexdigest()
        existing = self.repository.by_checksum(principal.tenant_id, checksum, document_type)
        if existing is not None:
            return existing, False

        document_id = str(uuid.uuid4())
        stored = self.artifact_store.put(principal.tenant_id, document_id, filename, content)
        document = KnowledgeDocument(
            id=document_id,
            tenant_id=principal.tenant_id,
            document_type=document_type,
            original_filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            checksum=checksum,
            artifact_key=stored.key,
            status="uploaded",
        )
        self.session.add(document)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            self.artifact_store.delete(stored.key)
            concurrent = self.repository.by_checksum(principal.tenant_id, checksum, document_type)
            if concurrent is None:
                raise
            return concurrent, False
        self.dispatcher.dispatch_knowledge(principal.tenant_id, document.id)
        # Inline dispatch uses its own session, so discard this request session's
        # cached ``uploaded`` instance before returning the processing result.
        self.session.expire_all()
        return self.get(principal, document.id), True

    def get(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        document = self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        return document

    def list(self, principal: Principal) -> list[KnowledgeDocument]:
        return self.repository.list_documents(principal.tenant_id)

    def reindex(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        document = self.get(principal, document_id)
        if document.status == "inactive":
            raise UnsupportedFileError("已停用文档不能重新索引")
        document.status = "uploaded"
        self.session.commit()
        self.dispatcher.dispatch_knowledge(principal.tenant_id, document.id)
        self.session.expire_all()
        return self.get(principal, document.id)

    def deactivate(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        document = self.get(principal, document_id)
        self.repository.deactivate(document)
        self.session.commit()
        return self.get(principal, document.id)
