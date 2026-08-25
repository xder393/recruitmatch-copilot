from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.support.application import create_sqlite_test_app
from tests.support.database import prepare_test_database


@pytest.fixture
def authenticated_client(tmp_path):
    settings = Settings(
        api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'jobs-api.db'}",
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    prepare_test_database(settings.database_url)
    app = create_sqlite_test_app(settings)
    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/bootstrap",
            json={
                "tenant_name": "Acme",
                "email": "admin@acme.test",
                "password": "correct horse battery staple",
            },
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@acme.test", "password": "correct horse battery staple"},
        )
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield client


def test_create_update_activate_and_list_job(authenticated_client):
    """Catches job routes bypassing version and lifecycle business rules."""
    profile = {
        "job_family": "ai_application",
        "level": "mid",
        "required_skills": ["Python", "FastAPI"],
        "preferred_skills": ["RAG"],
        "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
    }
    created = authenticated_client.post(
        "/api/v1/jobs",
        json={
            "title": "AI 应用开发工程师",
            "jd_text": "负责 RAG 应用，要求 Python 与 FastAPI",
            "profile": profile,
        },
    )
    assert created.status_code == 201
    assert created.json()["current_version"] == 1
    job_id = created.json()["id"]

    updated = authenticated_client.put(
        f"/api/v1/jobs/{job_id}",
        json={"jd_text": "负责 RAG 与 Agent 应用，要求 Python 与 FastAPI"},
    )
    assert updated.status_code == 200
    assert updated.json()["current_version"] == 2
    assert len(updated.json()["versions"]) == 2

    activated = authenticated_client.post(f"/api/v1/jobs/{job_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    listing = authenticated_client.get("/api/v1/jobs")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == job_id


def test_job_identifier_from_another_tenant_is_hidden(authenticated_client):
    """Catches API dependencies accepting a caller-selected tenant."""
    created = authenticated_client.post(
        "/api/v1/jobs",
        json={"title": "Private", "jd_text": "private JD", "profile": {}},
    )
    job_id = created.json()["id"]

    authenticated_client.headers.pop("Authorization")
    authenticated_client.post(
        "/api/v1/auth/bootstrap",
        json={
            "tenant_name": "Globex",
            "email": "admin@globex.test",
            "password": "another correct horse password",
        },
    )
    login = authenticated_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@globex.test", "password": "another correct horse password"},
    )
    authenticated_client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    response = authenticated_client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
