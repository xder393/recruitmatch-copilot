"""RecruitMatch relational database primitives."""

from __future__ import annotations

from typing import Tuple

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    """Declarative base shared by recruiting persistence models."""


def create_engine_and_session(database_url: str) -> Tuple[Engine, sessionmaker]:
    """Create an engine and session factory for an explicit database URL."""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)
