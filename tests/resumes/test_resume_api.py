from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.core.exceptions import ResourceNotFoundError
from app.main import create_app
from app.models.knowledge import KnowledgeChunk
from app.models.matching import MatchResult, MatchRun
from app.models.resumes import Resume


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


def test_failed_task_dispatch_is_visible_and_duplicate_upload_retries(resume_client):
    class BrokenDispatcher:
        def dispatch_resume(self, tenant_id, resume_id):
            raise RuntimeError("broker unavailable")

    working = resume_client.app.state.task_dispatcher
    resume_client.app.state.task_dispatcher = BrokenDispatcher()
    content = "Python retry".encode()
    failed = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("retry.txt", content, "text/plain")},
    )
    assert failed.status_code == 202
    assert failed.json()["status"] == "failed"
    assert failed.json()["error_code"] == "task_dispatch_failed"

    resume_client.app.state.task_dispatcher = working
    retried = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("retry.txt", content, "text/plain")},
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "succeeded"


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


def test_artifact_cleanup_failure_is_recorded_and_delete_can_retry(resume_client):
    class FailOnceStore:
        def __init__(self, delegate):
            self.delegate = delegate
            self.failed = False

        def put(self, *args, **kwargs):
            return self.delegate.put(*args, **kwargs)

        def read(self, *args, **kwargs):
            return self.delegate.read(*args, **kwargs)

        def delete(self, key):
            if not self.failed:
                self.failed = True
                raise OSError("storage unavailable")
            return self.delegate.delete(key)

    uploaded = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("cleanup.txt", b"private Python resume", "text/plain")},
    ).json()
    with resume_client.app.state.session_factory() as session:
        storage_key = session.get(Resume, uploaded["id"]).artifact.storage_key
    original = resume_client.app.state.artifact_store
    resume_client.app.state.artifact_store = FailOnceStore(original)

    assert resume_client.delete(f"/api/v1/resumes/{uploaded['id']}").status_code == 204
    assert original.read(storage_key) == b"private Python resume"
    with resume_client.app.state.session_factory() as session:
        assert session.get(Resume, uploaded["id"]).error_code == "artifact_delete_failed"

    assert resume_client.delete(f"/api/v1/resumes/{uploaded['id']}").status_code == 204
    with pytest.raises(ResourceNotFoundError):
        original.read(storage_key)
    with resume_client.app.state.session_factory() as session:
        assert session.get(Resume, uploaded["id"]).error_code is None


def test_list_and_soft_delete_hide_resume(resume_client):
    """Catches deleted candidate data remaining visible in recruiter workflows."""
    uploaded = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("delete-me.txt", "Python 经历".encode(), "text/plain")},
    )
    resume_id = uploaded.json()["id"]
    job = resume_client.post(
        "/api/v1/jobs",
        json={
            "title": "Python Engineer",
            "jd_text": "Python",
            "profile": {
                "job_family": "backend",
                "level": "mid",
                "required_skills": ["Python"],
                "preferred_skills": [],
                "min_experience_years": 0,
                "weights": {"skills": 1},
            },
        },
    ).json()
    resume_client.post(f"/api/v1/jobs/{job['id']}/activate")
    resume_client.post(f"/api/v1/resumes/{resume_id}/matches")

    listing = resume_client.get("/api/v1/resumes")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    assert resume_client.delete(f"/api/v1/resumes/{resume_id}").status_code == 204
    assert resume_client.get(f"/api/v1/resumes/{resume_id}").status_code == 404
    assert resume_client.get("/api/v1/resumes").json()["total"] == 0
    with resume_client.app.state.session_factory() as session:
        stored = session.get(Resume, resume_id)
        assert stored.profile == {}
        assert stored.artifact.extracted_text is None
        assert session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.source_type == "resume",
                KnowledgeChunk.source_id == resume_id,
            )
        ) is None
        run = session.scalar(select(MatchRun).where(MatchRun.resume_id == resume_id))
        result = session.scalar(select(MatchResult).where(MatchResult.run_id == run.id))
        assert result.evidence == []
        assert result.citations == []
        assert result.grounded_explanation == {}
        assert result.interview_questions == []
    reuploaded = resume_client.post(
        "/api/v1/resumes",
        files={"file": ("delete-me.txt", "Python 经历".encode(), "text/plain")},
    )
    assert reuploaded.status_code == 202
    assert reuploaded.json()["id"] != resume_id
