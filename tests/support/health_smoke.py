"""In-container HTTP health assertion for the explicitly synthetic live runtime."""

import os

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import Role
from app.main import create_app
from app.security.tokens import Principal, issue_access_token
from tests.support.application import DeterministicEmbeddingAdapter


def main():
    settings = Settings.load()
    assert not settings.ai_enabled and not settings.api_key
    app = create_app(settings, knowledge_embedder=DeterministicEmbeddingAdapter())
    with TestClient(app) as client:
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 200, "synthetic_dependencies_not_ready"
        token = issue_access_token(Principal("synthetic", "synthetic", Role.ADMIN), app.state.token_settings)
        response = client.get("/api/v1/health/system", headers={"Authorization": f"Bearer {token}"})
        body = response.json()
        assert response.status_code == 200
        assert body["beat"] == "fresh"
        assert body["worker"] == os.environ["EXPECTED_WORKER"]
        assert body["overall"] == ("ok" if body["worker"] == "fresh" else "degraded")
        print({"readiness": ready.status_code, **body})


if __name__ == "__main__":
    main()
