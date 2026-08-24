"""Recruiting knowledge upload, idempotency, and lifecycle rules."""

from __future__ import annotations

import hashlib
import uuid

from app.core.exceptions import AuthorizationError, ResourceNotFoundError, UnsupportedFileError
from app.domain.enums import Role
from app.models.knowledge import KnowledgeDocument
from app.repositories.ports import KnowledgeRepository, RepositoryConflictError
from app.repositories.unit_of_work import UnitOfWork
from app.resumes.extractors import FilePolicy
from app.security.tokens import Principal
from app.tasks.dispatcher import TaskDispatcher

_DOCUMENT_TYPES = {"policy", "interview_guide", "competency", "assessment_rubric"}


class KnowledgeDocumentService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        artifact_store,
        dispatcher: TaskDispatcher,
        file_policy: FilePolicy | None = None,
        *,
        uow: UnitOfWork,
    ):
        self.repository = repository
        self.uow = uow
        self.artifact_store = artifact_store
        self.dispatcher = dispatcher
        self.file_policy = file_policy or FilePolicy()

    def upload(
        self,
        principal: Principal,
        document_type: str,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> tuple[KnowledgeDocument, bool]:
        self._require_mutation(principal)
        if document_type not in _DOCUMENT_TYPES:
            raise UnsupportedFileError("不支持的招聘知识文档类型")
        self.file_policy.validate(filename, media_type, content)
        checksum = hashlib.sha256(content).hexdigest()
        existing = self.repository.by_checksum(principal.tenant_id, checksum, document_type)
        if existing is not None:
            if existing.status == "failed" and existing.error_code == "task_dispatch_failed":
                existing.status = "uploaded"
                existing.error_code = None
                existing.error_message = None
                self.uow.commit()
                self._dispatch(principal.tenant_id, existing)
                return self._reload(principal, existing.id), False
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
        self.repository.add(document)
        try:
            self.uow.commit()
        except RepositoryConflictError:
            self.uow.rollback()
            self.artifact_store.delete(stored.key)
            concurrent = self.repository.by_checksum(principal.tenant_id, checksum, document_type)
            if concurrent is None:
                raise
            return concurrent, False
        self._dispatch(principal.tenant_id, document)
        # Inline dispatch uses its own session, so discard this request session's
        # cached ``uploaded`` instance before returning the processing result.
        return self._reload(principal, document.id), True

    def get(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        document = self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        return document

    def list(self, principal: Principal) -> list[KnowledgeDocument]:
        return self.repository.list_documents(principal.tenant_id)

    def reindex(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        self._require_mutation(principal)
        document = self.get(principal, document_id)
        if document.status == "inactive":
            raise UnsupportedFileError("已停用文档不能重新索引")
        document.status = "uploaded"
        self.uow.commit()
        self._dispatch(principal.tenant_id, document)
        return self._reload(principal, document.id)

    def deactivate(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        self._require_mutation(principal)
        document = self.get(principal, document_id)
        self.repository.deactivate(document)
        self.uow.commit()
        return self.get(principal, document.id)

    @staticmethod
    def _require_mutation(principal: Principal) -> None:
        if principal.role not in {Role.ADMIN, Role.RECRUITER}:
            raise AuthorizationError("无权修改企业招聘知识库")

    def _dispatch(self, tenant_id: str, document: KnowledgeDocument) -> None:
        try:
            self.dispatcher.dispatch_knowledge(tenant_id, document.id)
        except Exception:
            document.status = "failed"
            document.error_code = "task_dispatch_failed"
            document.error_message = "知识处理任务投递失败，重试上传或重建可重新投递"
            self.uow.commit()

    def _reload(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        document = self.repository.reload_document(principal.tenant_id, document_id)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        return document
