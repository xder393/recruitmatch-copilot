from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.support.application import create_sqlite_test_app
from app.ai.gateway import ModelGatewayError
from app.ai.contracts import ModelRequest, ModelResponse
from pydantic import BaseModel
from app.services.ai_tracing import AITraceSink
from tests.support.database import prepare_test_database


class _Answer(BaseModel):
    value: str


def _trace_sink(session_factory):
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory

    return AITraceSink(SqlAlchemyUnitOfWorkFactory(session_factory))


@pytest.fixture
def operations_client(tmp_path):
    settings = Settings(
        api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'operations.db'}",
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    prepare_test_database(settings.database_url)
    app = create_sqlite_test_app(settings)
    with TestClient(app) as client:
        yield client


def _login(client, tenant, email, password):
    client.headers.pop("Authorization", None)
    client.post("/api/v1/auth/bootstrap", json={"tenant_name": tenant, "email": email, "password": password})
    token = client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"


def test_liveness_and_readiness_do_not_require_model_calls(operations_client):
    """Catches health checks loading embeddings or invoking paid models."""
    assert operations_client.get("/api/v1/health/live").json() == {"status": "alive"}
    ready = operations_client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "database": "ok",
        "schema": "ok",
        "vector": "ok",
        "redis": "ok",
        "bucket": "ok",
    }


def test_worker_stale_degrades_system_but_not_readiness(operations_client):
    """Missing Workers must not make the otherwise usable API unready."""
    _login(operations_client, "Acme", "admin@acme.test", "correct horse battery staple")
    assert operations_client.get("/api/v1/health/ready").status_code == 200
    response = operations_client.get("/api/v1/health/system")
    assert response.status_code == 200
    assert response.json()["worker"] == "stale"
    assert response.json()["overall"] == "degraded"
    assert response.json()["telemetry"] == "not_configured"
    assert {
        "api",
        "database",
        "redis",
        "minio",
        "worker",
        "beat",
        "ai",
        "telemetry",
        "overall",
    } <= response.json().keys()
    assert response.json()["api"] == "ok" and response.json()["minio"] == "ok"


def test_system_health_requires_authentication(operations_client):
    assert operations_client.get("/api/v1/health/system").status_code == 401


@pytest.mark.parametrize("role", ["recruiter", "lead"])
def test_system_health_denies_non_admin_before_probes(operations_client, role):
    from app.domain.enums import Role
    from app.security.tokens import Principal, issue_access_token

    token = issue_access_token(Principal("user", "tenant", Role(role)), operations_client.app.state.token_settings)
    response = operations_client.get("/api/v1/health/system", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


@pytest.mark.parametrize("dependency", ["database", "schema", "vector", "redis", "bucket"])
def test_hard_probe_failure_is_finite_unavailable_but_live_survives(operations_client, dependency):
    health = operations_client.app.state.health
    if dependency in {"database", "schema", "vector"}:
        health.database.statuses[dependency] = "unavailable"
    else:
        getattr(health, dependency).status = "unavailable"
    assert operations_client.get("/api/v1/health/live").json() == {"status": "alive"}
    ready = operations_client.get("/api/v1/health/ready")
    assert ready.status_code == 503 and ready.json()[dependency] == "unavailable"
    _login(operations_client, "Acme", "admin@acme.test", "correct horse battery staple")
    system = operations_client.get("/api/v1/health/system")
    assert system.status_code == 503 and system.json()["overall"] == "unavailable"
    assert system.json()["api"] == "ok"
    assert system.json()["minio"] == ("unavailable" if dependency == "bucket" else "ok")


def test_disabled_optional_components_do_not_fake_telemetry_or_degrade_live_runtime(operations_client):
    from tests.fakes.operations import FakeHeartbeats

    class FreshHeartbeats(FakeHeartbeats):
        def snapshot(self):
            return {**super().snapshot(), "worker": "fresh", "beat": "fresh", "worker_count": 1}

    health = operations_client.app.state.health
    health.heartbeats = FreshHeartbeats()
    assert health.system()["overall"] == "ok"
    assert health.system()["ai"] == "disabled"
    assert health.system()["telemetry"] == "not_configured"
    health.ai_enabled = True
    health.ai_configured = True
    assert health.system()["ai"] == "unknown" and health.system()["overall"] == "degraded"


def test_ai_status_reports_safe_degradation_without_credentials(operations_client):
    _login(operations_client, "Acme", "admin@acme.test", "correct horse battery staple")
    response = operations_client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["degraded"] is True
    assert body["fallback_mode"] == "rules-v1"
    assert "api_key" not in response.text.casefold()
    assert "sk-" not in response.text.casefold()


def test_ai_status_uses_recent_trace_health(tmp_path):
    settings = Settings(
        api_key="test-key",
        ai_enabled=True,
        database_url=f"sqlite:///{tmp_path / 'ai-health.db'}",
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )

    class NoCallModel:
        def generate(self, request):
            raise AssertionError("status endpoint must not probe a paid model")

    class Embedder:
        def embed_documents(self, texts):
            return [[1.0] for _ in texts]

        def embed_query(self, text):
            return [1.0]

    prepare_test_database(settings.database_url)
    app = create_sqlite_test_app(settings, structured_model=NoCallModel(), knowledge_embedder=Embedder())
    with TestClient(app) as client:
        _login(client, "Acme", "admin@acme.test", "correct horse battery staple")
        tenant_id = client.get("/api/v1/auth/me").json()["tenant_id"]
        request = ModelRequest(
            operation="semantic_project_match",
            prompt_version="semantic-project-v1",
            system="private",
            user="private",
            schema=_Answer,
        )
        _trace_sink(app.state.session_factory).failed(
            tenant_id,
            "semantic_match",
            "resume-id",
            ["resume-id", "job-id"],
            request,
            ModelGatewayError("timeout", True, attempts=2),
            20000,
        )
        body = client.get("/api/v1/ai/status").json()
        assert body["available"] is False
        assert body["degraded"] is True
        assert body["latest_status"] == "failed"
        assert body["latest_error"] == "timeout"
        assert body["latest_latency_ms"] == 20000


def test_ai_status_requires_authentication(operations_client):
    assert operations_client.get("/api/v1/ai/status").status_code == 401


def test_ai_status_ignores_other_tenant_model_failures(tmp_path):
    settings = Settings(
        api_key="test-key",
        ai_enabled=True,
        database_url=f"sqlite:///{tmp_path / 'tenant-ai-health.db'}",
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    app = create_sqlite_test_app(settings)
    request = ModelRequest(
        operation="semantic_project_match",
        prompt_version="semantic-project-v1",
        system="private",
        user="private",
        schema=_Answer,
    )
    response = ModelResponse(
        value=_Answer(value="ok"),
        provider="fake",
        model="fake",
        input_tokens=1,
        output_tokens=1,
        estimated_cost=0,
        latency_ms=1,
    )
    prepare_test_database(settings.database_url)
    with TestClient(app) as client:
        _login(client, "Acme", "admin@acme.test", "correct horse battery staple")
        acme_id = client.get("/api/v1/auth/me").json()["tenant_id"]
        _trace_sink(app.state.session_factory).succeeded(
            acme_id, "semantic_match", "resume", ["resume", "job"], request, response
        )
        _login(client, "Globex", "admin@globex.test", "another correct horse password")
        globex_id = client.get("/api/v1/auth/me").json()["tenant_id"]
        _trace_sink(app.state.session_factory).failed(
            globex_id,
            "semantic_match",
            "resume",
            ["resume", "job"],
            request,
            ModelGatewayError("timeout", True),
            20000,
        )
        _login(client, "Acme", "admin@acme.test", "correct horse battery staple")
        body = client.get("/api/v1/ai/status").json()
        assert body["available"] is True
        assert body["degraded"] is False
        assert body["latest_status"] == "succeeded"

        _trace_sink(app.state.session_factory).succeeded(
            acme_id,
            "semantic_match",
            "resume",
            ["resume", "job"],
            request,
            response,
            fallback_reason="invalid_semantic_citations",
        )
        rejected = client.get("/api/v1/ai/status").json()
        assert rejected["available"] is False
        assert rejected["degraded"] is True
        assert rejected["fallback_mode"] == "rules-v1"
        assert rejected["latest_status"] == "rejected"
        assert rejected["latest_error"] == "invalid_semantic_citations"


def test_analytics_are_tenant_scoped_and_never_return_resume_text(operations_client):
    """Catches cross-tenant aggregate leakage or PII appearing in operations responses."""
    _login(operations_client, "Acme", "admin@acme.test", "correct horse battery staple")
    operations_client.post(
        "/api/v1/resumes",
        files={"file": ("private.txt", "Python 私密候选人经历".encode(), "text/plain")},
    )
    acme = operations_client.get("/api/v1/analytics/summary")
    assert acme.status_code == 200
    assert acme.json()["resumes"] == 1
    assert "私密候选人经历" not in acme.text

    _login(operations_client, "Globex", "admin@globex.test", "another correct horse password")
    globex = operations_client.get("/api/v1/analytics/summary")
    assert globex.status_code == 200
    assert globex.json()["resumes"] == 0
