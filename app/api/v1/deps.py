"""Database, authentication, and authorization dependencies."""

from __future__ import annotations

from typing import Generator, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationError
from app.core.exceptions import AuthorizationError
from app.domain.enums import Role
from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from app.security.tokens import Principal, TokenSettings, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_health(request: Request):
    return request.app.state.health


def get_db(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        yield session


def get_unit_of_work(session: Session = Depends(get_db)) -> SqlAlchemyUnitOfWork:
    """Compose SQLAlchemy adapters at the HTTP boundary."""
    return SqlAlchemyUnitOfWork(session)


def get_identity_repository(uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work)):
    return uow.identities


def get_feedback_repository(uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work)):
    return uow.feedback


def get_job_repository(uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work)):
    return uow.jobs


def get_knowledge_repository(uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work)):
    return uow.knowledge


def get_matching_repository(uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work)):
    return uow.matching


def get_resume_repository(uow: SqlAlchemyUnitOfWork = Depends(get_unit_of_work)):
    return uow.resumes


def get_token_settings(request: Request) -> TokenSettings:
    return request.app.state.token_settings


def get_current_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    token_settings: TokenSettings = Depends(get_token_settings),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("请先登录")
    principal = decode_access_token(credentials.credentials, token_settings)
    request.state.principal = principal
    return principal


def get_admin_principal(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.role != Role.ADMIN:
        raise AuthorizationError("需要管理员权限")
    return principal
