from __future__ import annotations

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.config import Settings
from app.database import Base, create_engine_and_session
from app.database_migrations import upgrade_database
from app.main import create_app


class _Embedder:
    def embed_documents(self, texts):
        return [[1.0] for _ in texts]

    def embed_query(self, text):
        return [1.0]


def test_upgrade_database_explicit_url_wins_over_environment(monkeypatch, tmp_path):
    explicit_url = f"sqlite:///{tmp_path / 'explicit.db'}"
    environment_url = f"sqlite:///{tmp_path / 'environment.db'}"
    monkeypatch.setenv("DATABASE_URL", environment_url)

    upgrade_database(explicit_url)

    explicit_engine, _ = create_engine_and_session(explicit_url)
    environment_engine, _ = create_engine_and_session(environment_url)
    assert inspect(explicit_engine).has_table("alembic_version")
    assert not inspect(environment_engine).has_table("alembic_version")


def test_direct_alembic_uses_environment_before_ini_fallback(monkeypatch, tmp_path):
    environment_url = f"sqlite:///{tmp_path / 'environment.db'}"
    ini_fallback_url = f"sqlite:///{tmp_path / 'ini-fallback.db'}"
    monkeypatch.setenv("DATABASE_URL", environment_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", ini_fallback_url)

    command.upgrade(config, "head")

    environment_engine, _ = create_engine_and_session(environment_url)
    ini_fallback_engine, _ = create_engine_and_session(ini_fallback_url)
    assert inspect(environment_engine).has_table("alembic_version")
    assert not inspect(ini_fallback_engine).has_table("alembic_version")


def test_startup_repairs_unversioned_create_all_schema(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
