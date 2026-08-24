"""Explicit schema setup for tests that exercise application startup."""

from sqlalchemy import Engine

from app.database import Base, create_engine_and_session
from app.models import __all__ as _model_names


def prepare_test_database(database_url: str) -> Engine:
    """Create the current ORM schema without involving production startup."""
    assert _model_names
    engine, _ = create_engine_and_session(database_url)
    Base.metadata.create_all(engine)
    return engine
