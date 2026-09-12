"""Explicit operations Fakes for SQLite API tests."""

from app.operations.health import HealthService


class FakeDatabaseProbe:
    def __init__(self):
        self.statuses = {"database": "ok", "schema": "ok", "vector": "ok"}

    def check(self):
        return self.statuses.copy()


class FakeDependencyProbe:
    def __init__(self, status="ok"):
        self.status = status

    def check(self):
        return self.status


class FakeHeartbeats:
    def snapshot(self):
        return {
            "worker": "stale",
            "beat": "stale",
            "worker_count": 0,
            "worker_oldest_heartbeat_age_seconds": None,
            "beat_heartbeat_age_seconds": None,
        }


def fake_health(settings):
    return HealthService(
        FakeDatabaseProbe(),
        FakeDependencyProbe(),
        FakeDependencyProbe(),
        FakeHeartbeats(),
        ai_enabled=settings.ai_enabled,
        ai_configured=bool(settings.api_key),
    )
