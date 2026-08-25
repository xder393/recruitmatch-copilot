from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.support.application import create_sqlite_test_app
from tests.support.database import prepare_test_database


@pytest.fixture
def v1_client(tmp_path):
    settings = Settings(
        api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    prepare_test_database(settings.database_url)
    app = create_sqlite_test_app(settings)
    with TestClient(app) as client:
        yield client


def test_login_token_authorizes_current_user(v1_client):
    """Catches auth routes returning tokens that protected routes cannot use."""
    created = v1_client.post(
        "/api/v1/auth/bootstrap",
        json={
            "tenant_name": "Acme",
            "email": "admin@acme.test",
            "password": "correct horse battery staple",
        },
    )
    assert created.status_code == 201

    login = v1_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@acme.test", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = v1_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {
        "id": created.json()["id"],
        "email": "admin@acme.test",
        "role": "admin",
        "tenant_id": created.json()["tenant_id"],
        "tenant_name": "Acme",
    }


def test_current_user_rejects_missing_token(v1_client):
    """Catches accidentally exposing tenant identity endpoints anonymously."""
    response = v1_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
