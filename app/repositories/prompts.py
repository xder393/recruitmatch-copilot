"""Tenant-scoped prompt metadata lookup."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompts import PromptVersion


class PromptRepository:
    def __init__(self, session: Session):
        self.session = session

    def active(self, tenant_id: str, operation: str) -> PromptVersion | None:
        return self.session.scalar(
            select(PromptVersion).where(
                PromptVersion.tenant_id == tenant_id,
                PromptVersion.operation == operation,
                PromptVersion.is_active.is_(True),
            )
        )
