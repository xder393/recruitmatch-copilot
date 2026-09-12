"""Finite read-only operational composition, separate from business ports."""

from typing import Protocol


class DatabaseProbe(Protocol):
    def check(self) -> dict[str, str]: ...


class DependencyProbe(Protocol):
    def check(self) -> str: ...


class HeartbeatProbe(Protocol):
    def snapshot(self) -> dict: ...


class HealthService:
    def __init__(
        self,
        database: DatabaseProbe,
        redis: DependencyProbe,
        bucket: DependencyProbe,
        heartbeats: HeartbeatProbe,
        *,
        ai_enabled: bool = False,
        ai_configured: bool = False,
    ):
        self.database = database
        self.redis = redis
        self.bucket = bucket
        self.heartbeats = heartbeats
        self.ai_enabled = ai_enabled
        self.ai_configured = ai_configured

    def readiness(self) -> dict[str, str]:
        database = self.database.check()
        result = {name: database[name] for name in ("database", "schema", "vector")}
        result.update(redis=self.redis.check(), bucket=self.bucket.check())
        # Adapters return finite values; fail closed for a malformed injected probe.
        result = {
            name: value if value in {"ok", "unavailable", "incompatible"} else "unavailable"
            for name, value in result.items()
        }
        return {"status": "ready" if all(value == "ok" for value in result.values()) else "unavailable", **result}

    def system(self) -> dict:
        ready = self.readiness()
        heartbeat = self.heartbeats.snapshot()
        ai = "disabled" if not self.ai_enabled else ("unknown" if self.ai_configured else "not_configured")
        overall = "ok"
        if ready["status"] != "ready":
            overall = "unavailable"
        elif heartbeat["worker"] != "fresh" or heartbeat["beat"] != "fresh" or self.ai_enabled:
            overall = "degraded"
        return {
            "overall": overall,
            "api": "ok",  # This process is serving the request, independently of dependencies.
            "minio": ready["bucket"],
            **{k: v for k, v in ready.items() if k != "status"},
            **heartbeat,
            "ai": ai,
            "telemetry": "not_configured",
        }
