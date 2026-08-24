from __future__ import annotations

import pytest


@pytest.fixture
def auth_context(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.models import Job, JobTemplate, JobVersion, Tenant, User  # noqa: F401

    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(engine)
    return session_factory


def test_bootstrap_hashes_password_and_login_returns_tenant_principal(auth_context):
    """Catches plaintext passwords and tokens missing tenant identity."""
    from app.domain.enums import Role
    from app.security.tokens import TokenSettings, decode_access_token
    from app.services.auth import AuthService
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    settings = TokenSettings(secret_key="a-test-secret-that-is-at-least-32-bytes")
    with auth_context() as session:
        uow = SqlAlchemyUnitOfWork(session)
        service = AuthService(uow.identities, uow, settings)
        user = service.bootstrap("Acme", " Admin@Acme.Test ", "correct horse battery staple")
        tenant_id = user.tenant_id
        assert user.password_hash != "correct horse battery staple"
        token = service.login("admin@acme.test", "correct horse battery staple")

    principal = decode_access_token(token, settings)
    assert principal.user_id == user.id
    assert principal.tenant_id == tenant_id
    assert principal.role is Role.ADMIN


def test_login_rejects_wrong_password_with_generic_error(auth_context):
    """Catches password bypass and account-enumerating error details."""
    from app.core.exceptions import AuthenticationError
    from app.security.tokens import TokenSettings
    from app.services.auth import AuthService
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    settings = TokenSettings(secret_key="a-test-secret-that-is-at-least-32-bytes")
    with auth_context() as session:
        uow = SqlAlchemyUnitOfWork(session)
        service = AuthService(uow.identities, uow, settings)
        service.bootstrap("Acme", "admin@acme.test", "correct horse battery staple")
        with pytest.raises(AuthenticationError, match="邮箱或密码错误"):
            service.login("admin@acme.test", "wrong password")
        with pytest.raises(AuthenticationError, match="邮箱或密码错误"):
            service.login("missing@acme.test", "wrong password")
