"""Apply RecruitMatch migrations and recover legacy unversioned schemas."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def upgrade_database(database_url: str) -> None:
    """Upgrade to head, stamping the known pre-AI create_all schema when needed."""
    engine = create_engine(database_url)
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        tables = set(inspect(connection).get_table_names())
    engine.dispose()

    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    if current is None and "model_traces" in tables and "audit_logs" in tables:
        command.stamp(config, "20260819_04")
    command.upgrade(config, "head")
