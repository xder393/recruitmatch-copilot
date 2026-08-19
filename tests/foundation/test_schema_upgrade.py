from __future__ import annotations

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.config import Settings
from app.database import Base, create_engine_and_session
from app.main import create_app


class _Embedder:
    def embed_documents(self, texts):
        return [[1.0] for _ in texts]

    def embed_query(self, text):
        return [1.0]


def test_startup_repairs_unversioned_create_all_schema(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260819_04")

    engine, _ = create_engine_and_session(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))

    settings = Settings(
        database_url=database_url,
        artifact_dir=str(tmp_path / "resumes"),
        knowledge_artifact_dir=str(tmp_path / "knowledge"),
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    with TestClient(create_app(settings, knowledge_embedder=_Embedder())):
        pass

    columns = {item["name"] for item in inspect(engine).get_columns("model_traces")}
    assert {"operation", "request_fingerprint", "fallback_reason", "attempt_count"} <= columns
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260819_10"
