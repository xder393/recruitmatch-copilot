"""Tenant-scoped recruiting knowledge persistence."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeDocument
from app.models.retrieval import RecruitingChunk


class KnowledgeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_document(
        self,
        tenant_id: str,
        document_id: str,
        *,
        for_update: bool = False,
        include_deleted: bool = False,
    ) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
        if not include_deleted:
            statement = statement.where(KnowledgeDocument.lifecycle_status == "active")
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def by_checksum(self, tenant_id: str, checksum: str, document_type: str) -> KnowledgeDocument | None:
        return self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.checksum == checksum,
                KnowledgeDocument.document_type == document_type,
                KnowledgeDocument.lifecycle_status == "active",
            )
        )

    def list_documents(self, tenant_id: str) -> list[KnowledgeDocument]:
        return list(
            self.session.scalars(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.lifecycle_status == "active")
                .order_by(KnowledgeDocument.created_at.desc())
            )
        )

    def deactivate(self, document: KnowledgeDocument) -> None:
        document.status = "inactive"
        document.search_index_status = "inactive"
        self.session.execute(
            update(RecruitingChunk)
            .where(
                RecruitingChunk.tenant_id == document.tenant_id,
                RecruitingChunk.source_type == "knowledge_document",
                RecruitingChunk.source_id == document.id,
            )
            .values(is_active=False)
        )

    def add(self, document: KnowledgeDocument) -> None:
        self.session.add(document)

    def scrub_private_data(self, tenant_id, document, deleted_at) -> None:
        document.lifecycle_status = "deleted"
        document.deleted_at = deleted_at
        document.original_filename = None
        document.checksum = None
        document.size_bytes = 0
        document.error_code = None
        document.error_message = None
        document.search_index_status = "deleted"
        document.search_index_error_code = None
        document.search_indexed_at = None
        self.session.execute(
            delete(RecruitingChunk).where(
                RecruitingChunk.tenant_id == tenant_id,
                RecruitingChunk.source_type == "knowledge_document",
                RecruitingChunk.source_id == document.id,
            )
        )

    def reload_document(self, tenant_id: str, document_id: str) -> KnowledgeDocument | None:
        self.session.expire_all()
        return self.get_document(tenant_id, document_id)
