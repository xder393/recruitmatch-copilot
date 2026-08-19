from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_template_seed_expands_ten_families_to_three_levels_idempotently(tmp_path):
    """Catches incomplete demo coverage and duplicate templates after restart."""
    from sqlalchemy import func, select

    from app.database import Base, create_engine_and_session
    from app.models import Job, JobTemplate, JobVersion, Tenant, User  # noqa: F401
    from scripts.seed_job_templates import seed_templates

    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    with session_factory() as session:
        assert seed_templates(session) == 30
        assert seed_templates(session) == 0
        count = session.scalar(select(func.count()).select_from(JobTemplate))
        assert count == 30


def test_template_seed_cli_runs_from_repository_root(tmp_path):
    """Catches direct script execution losing the repository root from sys.path."""
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'seed-cli.db'}"
    result = subprocess.run(
        [sys.executable, "scripts/seed_job_templates.py"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "seeded_job_templates=30"
