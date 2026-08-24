"""Apply RecruitMatch migrations from the one-shot bootstrap process."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_URL_ATTRIBUTE = "recruitmatch_database_url"


def upgrade_database(database_url: str) -> None:
    """Upgrade an explicit database URL to the current migration head."""
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    config.attributes[_DATABASE_URL_ATTRIBUTE] = database_url
    command.upgrade(config, "head")
