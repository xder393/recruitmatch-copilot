"""Apply database migrations and seed idempotent reference data."""

from alembic import command
from alembic.config import Config

from seed_job_templates import main as seed_job_templates


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    seed_job_templates()


if __name__ == "__main__":
    main()
