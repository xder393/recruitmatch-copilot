from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError


def _database(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.models import Job, JobTemplate, JobVersion, Tenant, User  # noqa: F401
    from app.models.resumes import Resume, ResumeArtifact  # noqa: F401

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'resumes.db'}")
    Base.metadata.create_all(engine)
    return factory


def test_same_hash_can_exist_in_different_tenants(tmp_path):
    """Catches global deduplication leaking one tenant's artifact to another."""
    from app.domain.enums import ResumeStatus
    from app.models.identity import Tenant
    from app.models.resumes import Resume

    factory = _database(tmp_path)
    with factory() as session:
        acme, globex = Tenant(name="Acme"), Tenant(name="Globex")
        session.add_all([acme, globex])
        session.flush()
        session.add_all(
            [
                Resume(
                    tenant_id=acme.id,
                    sha256="abc",
                    original_filename="a.pdf",
                    media_type="application/pdf",
                    size_bytes=10,
                    status=ResumeStatus.QUEUED,
                ),
                Resume(
                    tenant_id=globex.id,
                    sha256="abc",
                    original_filename="a.pdf",
                    media_type="application/pdf",
                    size_bytes=10,
                    status=ResumeStatus.QUEUED,
                ),
            ]
        )
        session.commit()
        assert session.query(Resume).count() == 2


def test_same_hash_is_unique_inside_one_tenant(tmp_path):
    """Catches duplicate processing records for client upload retries."""
    from app.domain.enums import ResumeStatus
    from app.models.identity import Tenant
    from app.models.resumes import Resume

    factory = _database(tmp_path)
    with factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        values = dict(
            tenant_id=tenant.id,
            sha256="same",
            original_filename="a.txt",
            media_type="text/plain",
            size_bytes=6,
            status=ResumeStatus.QUEUED,
        )
        session.add_all([Resume(**values), Resume(**values)])
        with pytest.raises(IntegrityError):
            session.commit()
