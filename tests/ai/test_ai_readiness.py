from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models.operations import ModelTrace
from tests.ai.fakes import FakeStructuredModel


def _settings(tmp_path, **overrides):
    values = {
        "api_key": "",
        "database_url": f"sqlite:///{tmp_path / 'ai-status.db'}",
        "artifact_dir": str(tmp_path / "artifacts"),
        "jwt_secret": "a-test-secret-that-is-at-least-32-bytes",
    }
    values.update(overrides)
    return Settings(**values)


def _authenticated(client):
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "tenant_name": "Acme",
            "email": "admin@acme.test",
            "password": "correct horse battery staple",
        },
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@acme.test", "password": "correct horse battery staple"},
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"


def test_ai_status_is_disabled_without_disabling_core(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        body = client.get("/api/v1/ai/status").json()
        assert body == {
            "enabled": False,
            "degraded": True,
            "fallback_mode": "rules-v1",
            "provider": "openai-compatible",
            "model": "deepseek-chat",
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "core_available": True,
        }
        assert client.get("/api/v1/health/ready").status_code == 200


def test_enabled_ai_parser_persists_grounded_profile_and_safe_trace(tmp_path):
    model = FakeStructuredModel(
        {
            "resume_extract": {
                "skills": [
                    {"name": "Python", "evidence": {"start": 0, "end": 6, "text": "Python"}}
                ]
            }
        }
    )
    settings = _settings(tmp_path, api_key="test", ai_enabled=True)
    app = create_app(settings, structured_model=model)
    with TestClient(app) as client:
        _authenticated(client)
        response = client.post(
            "/api/v1/resumes",
            files={"file": ("resume.txt", b"Python developer", "text/plain")},
        )
        assert response.status_code == 202
        resume = client.get(f"/api/v1/resumes/{response.json()['id']}").json()
        assert resume["profile"]["skills"][0]["name"] == "Python"

        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ModelTrace)) == 1
            trace = session.scalar(select(ModelTrace))
            serialized = " ".join(str(value) for value in vars(trace).values())
            assert "Python developer" not in serialized
            assert trace.status == "succeeded"
