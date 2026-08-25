from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from tests.support.application import create_sqlite_test_app
from tests.support.database import prepare_test_database


def test_root_serves_recruiter_workflow_and_human_decision_notice(tmp_path):
    """Catches deployment accidentally serving the retired generic RAG chat UI."""
    settings = Settings(
        api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        artifact_dir=str(tmp_path / "artifacts"),
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    prepare_test_database(settings.database_url)
    app = create_sqlite_test_app(settings)
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "RecruitMatch Copilot" in response.text
    assert "岗位管理" in response.text
    assert "简历推荐" in response.text
    assert "最终招聘决定由招聘人员作出" in response.text
    assert "知识库问答" not in response.text
