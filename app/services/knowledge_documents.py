"""Recruiting knowledge upload, idempotency, and lifecycle rules."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import BinaryIO

from app.core.exceptions import AppError, AuthorizationError, ConflictError, ResourceNotFoundError, UnsupportedFileError
from app.artifacts.cleanup import cleanup_location
from app.artifacts.errors import storage_error_code
from app.artifacts.ports import ArtifactStore
from app.domain.artifacts import ArtifactErrorCode, ArtifactStatus
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
        document = self.repository.get_document(principal.tenant_id, document_id, for_update=True)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        if document.status == "inactive":
            raise UnsupportedFileError("已停用文档不能重新索引")
        artifact = (
            self.uow.artifacts.get(
                principal.tenant_id, "knowledge_document", document.id, document.artifact_id, for_update=True
            )
            if document.artifact_id
            else None
        )
        if artifact is None or artifact.status != ArtifactStatus.AVAILABLE:
            raise ConflictError("原始文件尚不可用", code="artifact_unavailable")
        document.status = "uploaded"
        document.processing_lease_owner = None
        document.processing_lease_expires_at = None
        document.processing_attempts = 0
        document.next_retry_at = None
        document.queued_at = datetime.now(timezone.utc)
        document.error_code = document.error_message = None
        self.uow.commit()
        self._dispatch(principal.tenant_id, document)
        return self._reload(principal, document.id)

    def deactivate(self, principal: Principal, document_id: str) -> KnowledgeDocument:
        self._require_mutation(principal)
        document = self.repository.get_document(principal.tenant_id, document_id, for_update=True)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        self.repository.deactivate(document)
        self.uow.commit()
        return self.get(principal, document.id)

    def delete(self, principal: Principal, document_id: str, *, confirm_tenant_history_redaction: bool = False) -> None:
        self._require_mutation(principal)
        if not confirm_tenant_history_redaction:
            raise ConflictError(
                "删除将清除本企业已有匹配结果内容，请明确确认", code="knowledge_privacy_confirmation_required"
            )
        self.uow.identities.lock_privacy_guard(principal.tenant_id)
        document = self.repository.get_document(principal.tenant_id, document_id, include_deleted=True, for_update=True)
        if document is None:
            raise ResourceNotFoundError("招聘知识文档不存在")
        location = None
        if document.artifact_id is not None:
            artifact = self.uow.artifacts.get(
                principal.tenant_id, "knowledge_document", document.id, document.artifact_id, for_update=True
            )
            if artifact is not None:
                location = self.uow.artifacts.resolve_location(
                    principal.tenant_id, "knowledge_document", document.id, artifact.id
                )
                scope = {"owner_type": "knowledge_document", "owner_id": document.id}
                if artifact.status == ArtifactStatus.PENDING:
                    self.uow.artifacts.mark_failed(
                        principal.tenant_id, artifact.id, ArtifactErrorCode.STORAGE_UNAVAILABLE, **scope
                    )
                if artifact.status in {ArtifactStatus.AVAILABLE, ArtifactStatus.FAILED, ArtifactStatus.CLEANUP_FAILED}:
                    self.uow.artifacts.mark_cleanup_pending(principal.tenant_id, artifact.id, **scope)
        if document.lifecycle_status != "deleted":
            self.repository.scrub_private_data(principal.tenant_id, document, datetime.now(timezone.utc))
            self.uow.matching.scrub_private_results(principal.tenant_id)
        self.uow.commit()
        if location is not None:
            cleanup_location(self.uow, self.artifact_store, location, "knowledge_document")

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
