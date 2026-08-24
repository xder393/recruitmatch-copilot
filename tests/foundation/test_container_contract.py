import os
import subprocess
import sys
from pathlib import Path


def test_runtime_image_declares_non_root_user():
    dockerfile = Path("Dockerfile").read_text()
    assert "FROM " in dockerfile and " AS builder" in dockerfile
    assert "USER recruitmatch" in dockerfile
    assert "requirements.txt" not in dockerfile


def test_worker_disables_the_api_healthcheck():
    compose = Path("docker-compose.yml").read_text()
    worker_service = compose.split("\n  worker:\n", maxsplit=1)[1].split("\n  test-unit:\n", maxsplit=1)[0]
    assert "healthcheck:\n      disable: true" in worker_service


def test_image_excludes_removed_legacy_namespaces():
    for namespace in ("agents", "rag", "storage", "tools", "embeddings"):
        assert not Path("app", namespace).exists()


def test_bootstrap_command_is_idempotent(tmp_path: Path):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{tmp_path / 'bootstrap.db'}"

    first = subprocess.run(
        [sys.executable, "scripts/bootstrap.py"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    second = subprocess.run(
        [sys.executable, "scripts/bootstrap.py"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert "seeded_job_templates=30" in first.stdout
    assert "seeded_job_templates=0" in second.stdout
