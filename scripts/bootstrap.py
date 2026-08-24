"""Apply database migrations and seed idempotent reference data."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database_migrations import upgrade_database  # noqa: E402
from seed_job_templates import main as seed_job_templates  # noqa: E402


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/recruitmatch.db").strip()
    upgrade_database(database_url)
    seed_job_templates()


if __name__ == "__main__":
    main()
