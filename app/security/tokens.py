"""JWT access-token contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app.core.exceptions import AuthenticationError
from app.domain.enums import Role


@dataclass(frozen=True)
class TokenSettings:
    secret_key: str
    issuer: str = "recruitmatch"
    audience: str = "recruitmatch-api"
    access_token_minutes: int = 30


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: Role


def issue_access_token(principal: Principal, settings: TokenSettings) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": principal.user_id,
        "tenant_id": principal.tenant_id,
        "role": principal.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": str(uuid.uuid4()),
        "iss": settings.issuer,
        "aud": settings.audience,
    }
    encoded = jwt.encode(claims, settings.secret_key, algorithm="HS256")
    return encoded.decode("ascii") if isinstance(encoded, bytes) else encoded


def decode_access_token(token: str, settings: TokenSettings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            issuer=settings.issuer,
            audience=settings.audience,
            options={"require": ["sub", "tenant_id", "role", "iat", "exp", "jti"]},
        )
        return Principal(
            user_id=str(claims["sub"]),
            tenant_id=str(claims["tenant_id"]),
            role=Role(claims["role"]),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise AuthenticationError("登录凭证无效或已过期") from exc
