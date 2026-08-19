from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def resume_client(tmp_path):
    settings = Settings(
        api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'resume-api.db'}",
        artifact_dir=str(tmp_path / "artifacts"),
        data_dir=str(tmp_path / "data"),
        index_dir=str(tmp_path / "data" / "index"),
        conversations_file=str(tmp_path / "data" / "conversations.json"),
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    app = create_app(settings)
    app.state._initialized = True
    with TestClient(app) as client:
        _switch_tenant(client, "Acme", "admin@acme.test", "correct horse battery staple")
        yield client


def _switch_tenant(client, name: str, email: str, password: str) -> None:
    client.headers.pop("Authorization", None)
    client.post(
        "/api/v1/auth/bootstrap",
        json={"tenant_name": name, "email": email, "password": password},
    )
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"


def test_duplicate_upload_is_idempotent_and_profile_is_queryable(resume_client):
    """Catches upload retries creating duplicate candidates or tasks."""
    content = "5 年 Python FastAPI RAG 项目经验".encode()
    first = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("candidate.txt", content, "text/plain")},
    )
    second = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("copy.txt", content, "text/plain")},
    )

    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    detail = resume_client.get(f"/api/v1/resumes/{first.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert [item["name"] for item in detail.json()["profile"]["skills"]] == ["Python", "FastAPI", "RAG"]


def test_resume_identifier_from_another_tenant_is_hidden(resume_client):
    """Catches IDOR reads across enterprise boundaries."""
    uploaded = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("private.txt", "Python 私密经历".encode(), "text/plain")},
    )
    resume_id = uploaded.json()["id"]

    _switch_tenant(resume_client, "Globex", "admin@globex.test", "another correct horse password")
    response = resume_client.get(f"/api/v1/resumes/{resume_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_list_and_soft_delete_hide_resume(resume_client):
    """Catches deleted candidate data remaining visible in recruiter workflows."""
    uploaded = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("delete-me.txt", "Python 经历".encode(), "text/plain")},
    )
    resume_id = uploaded.json()["id"]

    listing = resume_client.get("/api/v1/resumes")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    assert resume_client.delete(f"/api/v1/resumes/{resume_id}").status_code == 204
    assert resume_client.get(f"/api/v1/resumes/{resume_id}").status_code == 404
    assert resume_client.get("/api/v1/resumes").json()["total"] == 0
