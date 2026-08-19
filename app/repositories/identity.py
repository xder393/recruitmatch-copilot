"""Identity persistence operations."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Role
from app.models.identity import Tenant, User


class IdentityRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_user_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def get_user(self, user_id: str, tenant_id: str) -> User | None:
        return self.session.scalar(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id, User.is_active.is_(True))
        )

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.session.scalar(select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))

    def add_tenant_admin(self, tenant_name: str, email: str, password_hash: str) -> User:
        tenant = Tenant(name=tenant_name)
        user = User(email=email, password_hash=password_hash, role=Role.ADMIN, tenant=tenant)
        self.session.add(user)
        return user
