"""Readiness evidence uses real Compose dependencies and no inference."""

from dataclasses import replace
from time import monotonic


def test_production_readiness_checks_all_dependencies():
    from app.config import Settings
    from app.operations.probes import build_health

    health = build_health(Settings.load())
    assert health.readiness() == {
        "status": "ready",
        "database": "ok",
        "schema": "ok",
        "vector": "ok",
        "redis": "ok",
        "bucket": "ok",
    }
    system = health.system()
    assert system["api"] == "ok" and system["minio"] == "ok"


def test_bad_database_is_bounded_and_sanitized():
    from app.operations.probes import PostgreSQLProbe

    start = monotonic()
    result = PostgreSQLProbe("postgresql+psycopg://invalid:private@127.0.0.1:1/private").check()
    assert result == {"database": "unavailable", "schema": "unavailable", "vector": "unavailable"}
    assert monotonic() - start < 5


def test_bucket_probe_uses_app_permissions_and_denied_identity_fails():
    from app.config import Settings
    from app.artifacts.s3 import S3Settings
    from app.operations.probes import BucketProbe, build_health

    settings = S3Settings.from_env()
    assert BucketProbe(settings).check() == "ok"
    denied = BucketProbe(replace(settings, secret_access_key="invalid-synthetic"))
    assert denied.check() == "unavailable"
    health = build_health(Settings.load())
    health.bucket = denied
    system = health.system()
    assert system["minio"] == "unavailable" and system["overall"] == "unavailable"
    assert system["api"] == "ok"


def test_wrong_schema_version_is_not_ready(postgres_engine):
    from app.operations.probes import PostgreSQLProbe
    from tests.support.migrations import isolated_migration_database

    with isolated_migration_database(postgres_engine, revision="20260912_18") as (engine, _):
        result = PostgreSQLProbe(engine.url).check()
        assert result == {"database": "ok", "schema": "incompatible", "vector": "ok"}


def test_missing_vector_and_schema_fail_without_bootstrap_or_probe_writes(postgres_engine):
    from app.operations.probes import PostgreSQLProbe
    from tests.support.migrations import isolated_migration_database
    from sqlalchemy import text

    with isolated_migration_database(postgres_engine, revision="base") as (engine, _):
        result = PostgreSQLProbe(engine.url).check()
        assert result == {"database": "ok", "schema": "incompatible", "vector": "incompatible"}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 0


def test_redis_failure_is_unknown_heartbeats_not_zero_workers():
    from app.operations.heartbeats import OperationsHeartbeats
    from app.operations.probes import RedisProbe

    url = "redis://127.0.0.1:1/0"
    assert RedisProbe(url).check() == "unavailable"
    result = OperationsHeartbeats(url).snapshot()
    assert result["worker"] == "unknown" and result["worker_count"] is None
    assert result["beat"] == "unknown" and result["worker_oldest_heartbeat_age_seconds"] is None


def test_worker_registry_aggregates_processes_and_removes_only_stopped_owner():
    from uuid import uuid4
    from app.operations.heartbeats import OperationsHeartbeats
    from app.config import Settings

    heartbeats = OperationsHeartbeats(Settings.load().celery_broker_url, namespace="test:" + uuid4().hex)
    assert heartbeats.snapshot()["worker_count"] == 0
    assert heartbeats.snapshot()["worker_oldest_heartbeat_age_seconds"] is None
    try:
        heartbeats.pulse_worker("worker-a")
        heartbeats.pulse_worker("worker-b")
        heartbeats.pulse_beat()
        result = heartbeats.snapshot()
        assert result["worker"] == result["beat"] == "fresh" and result["worker_count"] == 2
        assert 0 <= result["worker_oldest_heartbeat_age_seconds"] < 3
        heartbeats.remove_worker("worker-a")
        assert heartbeats.snapshot()["worker_count"] == 1
    finally:
        heartbeats.remove_worker("worker-a")
        heartbeats.remove_worker("worker-b")


def test_stale_registry_data_reports_age_and_new_pulse_prunes_retention():
    from uuid import uuid4
    from app.operations.heartbeats import OperationsHeartbeats, redis_client
    from app.config import Settings

    heartbeats = OperationsHeartbeats(Settings.load().celery_broker_url, namespace="test:" + uuid4().hex)
    with redis_client(heartbeats.url) as client:
        now = client.time()[0]
        client.zadd(heartbeats.worker_key, {"stale": now - 40, "expired": now - 100})
        client.expire(heartbeats.worker_key, 90)
        client.set(heartbeats.beat_key, str(now - 40), ex=90)
    result = heartbeats.snapshot()
    assert result["worker"] == "stale" and result["worker_count"] == 0
    assert result["worker_oldest_heartbeat_age_seconds"] >= 100
    assert result["beat"] == "stale"
    heartbeats.pulse_worker("fresh")
    result = heartbeats.snapshot()
    assert result["worker_count"] == 1 and 40 <= result["worker_oldest_heartbeat_age_seconds"] < 90
    heartbeats.remove_worker("fresh")
    heartbeats.remove_worker("stale")


def test_real_api_health_keeps_auth_when_hard_dependencies_fail(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from app.operations.probes import build_health, RedisProbe
    from app.security.tokens import Principal, issue_access_token
    from app.domain.enums import Role
    from tests.support.application import DeterministicEmbeddingAdapter

    settings = Settings.load()
    health = build_health(settings)
    health.redis = RedisProbe("redis://127.0.0.1:1/0")
    app = create_app(settings, knowledge_embedder=DeterministicEmbeddingAdapter(), health=health)
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/api/v1/health/system").status_code == 401
        for role, expected in ((Role.RECRUITER, 403), (Role.ADMIN, 503)):
            token = issue_access_token(Principal("synthetic-user", "synthetic-tenant", role), app.state.token_settings)
            response = client.get("/api/v1/health/system", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == expected
            assert "127.0.0.1" not in response.text and "synthetic-tenant" not in response.text
