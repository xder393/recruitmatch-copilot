"""Recruiting knowledge upload, idempotency, and lifecycle rules."""

from __future__ import annotations

import uuid
from typing import BinaryIO

from app.core.exceptions import AppError, AuthorizationError, ConflictError, ResourceNotFoundError, UnsupportedFileError
from app.artifacts.cleanup import cleanup_location
from app.artifacts.errors import storage_error_code
from app.artifacts.ports import ArtifactStore
from app.domain.artifacts import ArtifactStatus
from app.domain.enums import Role
from app.models.knowledge import KnowledgeDocument
from app.repositories.ports import KnowledgeRepository
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.resumes.extractors import FilePolicy, PreparedUpload
from app.security.tokens import Principal
from app.tasks.dispatcher import TaskDispatcher

_DOCUMENT_TYPES = {"policy", "interview_guide", "competency", "assessment_rubric"}


class KnowledgeDocumentService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        artifact_store: ArtifactStore,
        dispatcher: TaskDispatcher,
        file_policy: FilePolicy | None = None,
        *,
        uow: RecruitingUnitOfWork,
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
        content: bytes | BinaryIO,
    ) -> tuple[KnowledgeDocument, bool]:
        self._require_mutation(principal)
        if document_type not in _DOCUMENT_TYPES:
            raise UnsupportedFileError("不支持的招聘知识文档类型")
        with self.file_policy.prepare(filename, media_type, content) as prepared:
            return self._upload(principal, document_type, filename, media_type, prepared)

    def _upload(self, principal, document_type, filename, media_type, prepared: PreparedUpload):
        checksum, size = prepared.sha256, prepared.size_bytes
        document_id = str(uuid.uuid4())
        artifact = self.uow.artifacts.claim_upload(
            principal.tenant_id, "knowledge_document", document_id, checksum, media_type, size
        )
        if artifact.owner_id != document_id:
            # claim_upload holds the winning Artifact lock. Read the Source
            # without a lock to avoid inverting Source→Artifact lock order.
            existing = self.repository.get_document(principal.tenant_id, artifact.owner_id)
            self.uow.commit()
            if existing is None:
                raise ResourceNotFoundError("招聘知识文档不存在")
            if existing.document_type != document_type:
                raise ConflictError("相同文件已使用其他文档类型", code="knowledge_document_type_conflict")
            return existing, False
        document: KnowledgeDocument | None = KnowledgeDocument(
            id=artifact.owner_id,
            tenant_id=principal.tenant_id,
            document_type=document_type,
            original_filename=filename,
            media_type=media_type,
            size_bytes=size,
            checksum=checksum,
            artifact_id=artifact.id,
            status="uploaded",
        )
        self.repository.add(document)
        location = self.uow.artifacts.resolve_location(
            principal.tenant_id, "knowledge_document", artifact.owner_id, artifact.id
        )
        self.uow.commit()
        try:
            self.artifact_store.put(location, prepared.stream, size, checksum)
        except Exception as exc:
            code = storage_error_code(exc)
            document = self.repository.get_document(principal.tenant_id, location.owner_id, for_update=True)
            current = self.uow.artifacts.get(
                principal.tenant_id, "knowledge_document", location.owner_id, location.artifact_id, for_update=True
            )
            if document is not None and current is not None and current.status == ArtifactStatus.PENDING:
                self.uow.artifacts.mark_failed(
                    principal.tenant_id,
                    location.artifact_id,
                    code,
                    owner_type="knowledge_document",
                    owner_id=location.owner_id,
                )
                if document.status != "inactive":
                    document.status = "failed"
                    document.error_code = code.value
                    document.error_message = "知识文件上传失败"
                self.uow.commit()
            else:
                self.uow.rollback()
                if current is not None and current.status in {
                    ArtifactStatus.CLEANUP_PENDING,
                    ArtifactStatus.CLEANUP_FAILED,
                    ArtifactStatus.DELETED,
                }:
                    cleanup_location(self.uow, self.artifact_store, location, "knowledge_document")
            raise AppError("知识文件上传失败", code=code.value, status_code=503) from exc
        document = self.repository.get_document(principal.tenant_id, location.owner_id, for_update=True)
        current = self.uow.artifacts.get(
            principal.tenant_id, "knowledge_document", location.owner_id, location.artifact_id, for_update=True
        )
        if (
            document is None
            or current is None
            or current.status not in {ArtifactStatus.PENDING, ArtifactStatus.AVAILABLE}
        ):
            self.uow.rollback()
            cleanup_location(self.uow, self.artifact_store, location, "knowledge_document")
            raise ResourceNotFoundError("招聘知识文档不存在")
        self.uow.artifacts.mark_available(
            principal.tenant_id, location.artifact_id, owner_type="knowledge_document", owner_id=location.owner_id
        )
        self.uow.commit()
        if document.status == "inactive":
            return self._reload(principal, document.id), True
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
            current = self.repository.get_document(tenant_id, document.id, for_update=True)
            if current is None or current.status != "uploaded":
                self.uow.rollback()
                return
            current.error_code = "task_dispatch_failed"
            current.error_message = "知识处理任务等待恢复投递"
            self.uow.commit()

    def _reload(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        document = self.repository.reload_document(principal.tenant_id, document_id)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        return document
