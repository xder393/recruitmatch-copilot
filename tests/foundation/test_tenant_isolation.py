from __future__ import annotations

import pytest


def test_other_tenant_cannot_discover_job_by_identifier(tmp_path):
    """Catches IDOR reads that omit the authenticated tenant predicate."""
    from app.core.exceptions import ResourceNotFoundError
    from app.database import Base, create_engine_and_session
    from app.domain.enums import Role
    from app.models import Job, JobTemplate, JobVersion, Tenant, User  # noqa: F401
    from app.security.tokens import Principal
    from app.services.jobs import JobService

    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'isolation.db'}")
    Base.metadata.create_all(engine)
    with session_factory() as session:
        acme = Tenant(name="Acme")
        globex = Tenant(name="Globex")
        session.add_all([acme, globex])
        session.commit()
        acme_principal = Principal(user_id="acme-admin", tenant_id=acme.id, role=Role.ADMIN)
        globex_principal = Principal(user_id="globex-admin", tenant_id=globex.id, role=Role.ADMIN)

        service = JobService(session)
        private_job = service.create_job(acme_principal, "Private role", "secret requirements", {})

        with pytest.raises(ResourceNotFoundError):
            service.get_job(globex_principal, private_job.id)
        assert service.list_jobs(globex_principal) == []
