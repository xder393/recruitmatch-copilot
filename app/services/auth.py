"""Tenant bootstrap and login business rules."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationError, ConflictError
from app.domain.enums import Role
from app.models.identity import User
from app.repositories.identity import IdentityRepository
from app.security.passwords import hash_password, verify_password
from app.security.tokens import Principal, TokenSettings, issue_access_token


class AuthService:
    def __init__(self, session: Session, token_settings: TokenSettings):
        self.session = session
        self.token_settings = token_settings
        self.identities = IdentityRepository(session)

    def bootstrap(self, tenant_name: str, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        if self.identities.find_user_by_email(normalized_email) is not None:
            raise ConflictError("该邮箱已注册")
        user = self.identities.add_tenant_admin(tenant_name.strip(), normalized_email, hash_password(password))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("该邮箱已注册") from exc
        self.session.refresh(user)
        return user

    def login(self, email: str, password: str) -> str:
        user = self.identities.find_user_by_email(email.strip().lower())
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise AuthenticationError("邮箱或密码错误")
        principal = Principal(user_id=user.id, tenant_id=user.tenant_id, role=Role(user.role))
        return issue_access_token(principal, self.token_settings)
