"""Fixtures for integration tests that require the Compose PostgreSQL service."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import Engine, create_engine, text


@pytest.fixture(scope="session")
def postgres_engine() -> Engine:
    database_url = os.environ["DATABASE_URL"]
    if not database_url.startswith("postgresql"):
        pytest.fail("integration tests require the Compose PostgreSQL database")

    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    yield engine
    engine.dispose()
