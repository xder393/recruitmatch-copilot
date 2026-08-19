from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _login(client, tenant, email, password):
    client.headers.pop("Authorization", None)
    client.post(
        "/api/v1/auth/bootstrap",
        json={"tenant_name": tenant, "email": email, "password": password},
    )
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"


@pytest.fixture
def matching_client(tmp_path):
    app = create_app(
        Settings(
            api_key="test-key",
            database_url=f"sqlite:///{tmp_path / 'matching-api.db'}",
            artifact_dir=str(tmp_path / "artifacts"),
            data_dir=str(tmp_path / "data"),
            index_dir=str(tmp_path / "data" / "index"),
            conversations_file=str(tmp_path / "data" / "conversations.json"),
            jwt_secret="a-test-secret-that-is-at-least-32-bytes",
        )
    )
    app.state._initialized = True
    with TestClient(app) as client:
        _login(client, "Acme", "admin@acme.test", "correct horse battery staple")
        for title, required, preferred in [
            ("AI 应用", ["Python", "FastAPI"], ["RAG"]),
            ("后端", ["Python", "MySQL"], ["Redis"]),
            ("数据", ["Python", "SQL"], ["Spark"]),
        ]:
            created = client.post(
                "/api/v1/jobs",
                json={
                    "title": title,
                    "jd_text": title,
                    "profile": {
                        "job_family": "tech",
                        "level": "mid",
                        "required_skills": required,
                        "preferred_skills": preferred,
                        "min_experience_years": 3,
                        "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
                    },
                },
            )
            client.post(f"/api/v1/jobs/{created.json()['id']}/activate")
        uploaded = client.post(
            "/api/v1/resumes",
            files={"file": ("candidate.txt", "5 年 Python FastAPI RAG".encode(), "text/plain")},
        )
        yield client, uploaded.json()["id"]


def test_match_api_returns_explainable_top_three_and_accepts_feedback(matching_client):
    """Catches a recommendation API omitting evidence or human review capture."""
    client, resume_id = matching_client
    response = client.post(f"/api/v1/resumes/{resume_id}/matches")
    assert response.status_code == 201
    run = response.json()
    assert run["algorithm_version"] == "rules-v1"
    assert run["results"][0]["rule_score"] is None
    assert [item["rank"] for item in run["results"]] == [1, 2, 3]
    assert run["results"][0]["job_title"] == "AI 应用"
    assert [item["text"] for item in run["results"][0]["evidence"]] == ["Python", "FastAPI", "RAG"]

    result_id = run["results"][0]["id"]
    feedback = client.post(
        f"/api/v1/match-results/{result_id}/feedback",
        json={"action": "confirm", "reason": "证据与岗位要求一致"},
    )
    assert feedback.status_code == 201
    assert feedback.json()["action"] == "confirm"


def test_hybrid_mode_persists_score_components(matching_client):
    from app.ai.semantic_matching import SemanticProjectScore
    from app.matching.engine import MatchingEngine
    from app.matching.hybrid import HybridMatchingEngine

    class Semantic:
        def score(self, tenant_id, resume_id, job_version_id):
            return SemanticProjectScore(
                score=0.75,
                rationale="项目证据匹配",
                resume_citation_ids=[f"resume-{resume_id}"],
                job_citation_ids=[f"job-{job_version_id}"],
            )

    client, resume_id = matching_client
    client.app.state.hybrid_matching_engine = HybridMatchingEngine(MatchingEngine(), Semantic())
    response = client.post(f"/api/v1/resumes/{resume_id}/matches?mode=hybrid-v1")
    assert response.status_code == 201
    body = response.json()
    assert body["algorithm_version"] == "hybrid-v1"
    assert body["results"][0]["rule_score"] is not None
    assert body["results"][0]["semantic_score"] == 0.75
    assert body["results"][0]["citations"]


def test_match_run_is_hidden_from_another_tenant(matching_client):
    """Catches recommendation result IDOR across tenants."""
    client, resume_id = matching_client
    run_id = client.post(f"/api/v1/resumes/{resume_id}/matches").json()["id"]
    _login(client, "Globex", "admin@globex.test", "another correct horse password")
    assert client.get(f"/api/v1/match-runs/{run_id}").status_code == 404
