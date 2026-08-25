from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.config import Settings
from app.database import create_engine_and_session
from tests.support.application import create_sqlite_test_app
from tests.support.database import prepare_test_database


class _Embedder:
    def embed_documents(self, texts):
        return [[1.0] for _ in texts]

    def embed_query(self, text):
        return [1.0]


def test_sqlite_test_schema_setup_never_runs_alembic(monkeypatch, tmp_path):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("SQLite test setup must not execute Alembic")

    monkeypatch.setattr(command, "upgrade", fail_if_called)
    engine = prepare_test_database(f"sqlite:///{tmp_path / 'unit.db'}")

    assert "resumes" in inspect(engine).get_table_names()


def test_pgvector_revision_rejects_non_postgresql_dialects(monkeypatch):
    path = Path("alembic/versions/20260824_11_pgvector_recruiting_chunks.py")
    spec = importlib.util.spec_from_file_location("pgvector_revision", path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "sqlite"})()})()
    monkeypatch.setattr(revision.op, "get_bind", lambda: bind)
    ddl_calls = []

    def fail_if_ddl_runs(*args, **kwargs):
        ddl_calls.append((args, kwargs))
        raise AssertionError("non-PostgreSQL revision must fail before issuing DDL")

    for operation in ("execute", "alter_column", "add_column", "create_table", "create_index"):
        monkeypatch.setattr(revision.op, operation, fail_if_ddl_runs)

    with pytest.raises(RuntimeError, match="requires PostgreSQL with pgvector"):
        revision.upgrade()
    assert ddl_calls == []


def test_application_startup_does_not_write_schema(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"
    settings = Settings(
        database_url=database_url,
        artifact_dir=str(tmp_path / "resumes"),
        knowledge_artifact_dir=str(tmp_path / "knowledge"),
        jwt_secret="a-test-secret-that-is-at-least-32-bytes",
    )
    with TestClient(create_sqlite_test_app(settings, knowledge_embedder=_Embedder())):
        pass

    engine, _ = create_engine_and_session(database_url)
    assert inspect(engine).get_table_names() == []
