"""Tenant bootstrap, login, and current-user endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db, get_token_settings
from app.api.v1.schemas import BootstrapRequest, CurrentUserResponse, LoginRequest, TokenResponse
from app.core.exceptions import AuthenticationError
from app.repositories.identity import IdentityRepository
from app.security.tokens import Principal, TokenSettings
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["RecruitMatch Auth"])


@router.post("/bootstrap", response_model=CurrentUserResponse, status_code=status.HTTP_201_CREATED)
def bootstrap(
    payload: BootstrapRequest,
    session: Session = Depends(get_db),
    token_settings: TokenSettings = Depends(get_token_settings),
):
    user = AuthService(session, token_settings).bootstrap(payload.tenant_name, payload.email, payload.password)
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=user.tenant.name,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    session: Session = Depends(get_db),
    token_settings: TokenSettings = Depends(get_token_settings),
):
    token = AuthService(session, token_settings).login(payload.email, payload.password)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUserResponse)
def current_user(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    identities = IdentityRepository(session)
    user = identities.get_user(principal.user_id, principal.tenant_id)
    tenant = identities.get_tenant(principal.tenant_id)
    if user is None or tenant is None:
        raise AuthenticationError("登录凭证无效或账户已停用")
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
    )
