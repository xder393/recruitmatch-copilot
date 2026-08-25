"""Tenant-scoped recruiting knowledge persistence."""

from __future__ import annotations

from sqlalchemy import select, update
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
    ) -> KnowledgeDocument | None:
        statement = select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def by_checksum(self, tenant_id: str, checksum: str, document_type: str) -> KnowledgeDocument | None:
        return self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.checksum == checksum,
                KnowledgeDocument.document_type == document_type,
            )
        )

    def list_documents(self, tenant_id: str) -> list[KnowledgeDocument]:
        return list(
            self.session.scalars(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.tenant_id == tenant_id)
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

    def reload_document(self, tenant_id: str, document_id: str) -> KnowledgeDocument | None:
        self.session.expire_all()
        return self.get_document(tenant_id, document_id)
