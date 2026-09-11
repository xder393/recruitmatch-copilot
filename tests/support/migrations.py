"""Disposable PostgreSQL databases for historical migration assertions."""

from contextlib import contextmanager
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


@contextmanager
def isolated_migration_database(engine, revision="20260911_14"):
    name = "cp3_migration_" + uuid4().hex
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name)
    isolated = create_engine(url)
    config = Config("alembic.ini")
    config.attributes["recruitmatch_database_url"] = url.render_as_string(hide_password=False)
    try:
        command.upgrade(config, revision)
        yield isolated, config
    finally:
        isolated.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def seed_legacy_resume(engine, *, anchored=True):
    tenant, owner, artifact, legacy = (str(uuid4()) for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants(id,name,is_active,created_at) VALUES (:t,'synthetic',true,now())"), {"t": tenant}
        )
        if anchored:
            connection.execute(
                text("""
                INSERT INTO artifacts(id,tenant_id,owner_type,owner_id,sha256,media_type,size_bytes,
                    status,created_at,updated_at)
                VALUES (:a,:t,'resume',:o,:sha,'text/plain',6,'AVAILABLE',now(),now())
            """),
                {"a": artifact, "t": tenant, "o": owner, "sha": "a" * 64},
            )
        connection.execute(
            text("""
            INSERT INTO resumes(id,tenant_id,sha256,original_filename,media_type,size_bytes,status,profile,
                created_at,updated_at,search_index_status,active_index_generation,artifact_id)
            VALUES (:o,:t,:sha,'legacy.txt','text/plain',6,'SUCCEEDED','{}',now(),now(),'ready',1,:a)
        """),
            {"o": owner, "t": tenant, "sha": "a" * 64, "a": artifact if anchored else None},
        )
        connection.execute(
            text("""
            INSERT INTO resume_artifacts(id,resume_id,storage_key,extracted_text,created_at)
            VALUES (:l,:o,:key,'legacy',now())
        """),
            {"l": legacy, "o": owner, "key": "synthetic/" + legacy},
        )
    return tenant, owner, artifact, legacy
