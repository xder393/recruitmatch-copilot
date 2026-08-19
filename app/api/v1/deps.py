"""Database, authentication, and authorization dependencies."""
from __future__ import annotations

from typing import Generator, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationError
from app.security.tokens import Principal, TokenSettings, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        yield session


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
