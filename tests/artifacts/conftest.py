"""Artifact persistence tests run only against the disposable PostgreSQL service."""

import os

import pytest
from sqlalchemy import create_engine


@pytest.fixture(scope="session")
def postgres_engine():
    url = os.environ["DATABASE_URL"]
    if not url.startswith("postgresql"):
        pytest.fail("Artifact persistence tests require Compose PostgreSQL")
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()
