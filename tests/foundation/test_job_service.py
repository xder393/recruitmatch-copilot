from __future__ import annotations

import pytest


VALID_PROFILE = {
    "job_family": "ai_application",
    "level": "mid",
    "required_skills": ["Python", "FastAPI"],
    "preferred_skills": ["RAG"],
    "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
}


@pytest.fixture
def job_context(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.domain.enums import Role
    from app.models import Job, JobTemplate, JobVersion, Tenant, User  # noqa: F401
    from app.security.tokens import Principal

    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        user = User(
            tenant=tenant,
            email="admin@acme.test",
            password_hash="not-used",
            role=Role.ADMIN,
        )
        session.add(user)
        session.commit()
        principal = Principal(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
    return session_factory, principal


def test_updating_job_appends_immutable_version(job_context):
    """Catches overwriting the JD used by historical match results."""
    from app.services.jobs import JobService

    session_factory, principal = job_context
    with session_factory() as session:
        service = JobService(session)
        job = service.create_job(principal, "AI Engineer", "Python", VALID_PROFILE)
        updated = service.update_job(principal, job.id, jd_text="Python and RAG")

        assert updated.current_version == 2
        assert [version.jd_text for version in updated.versions] == ["Python", "Python and RAG"]
        assert updated.versions[0].profile == VALID_PROFILE
        assert updated.versions[1].profile == VALID_PROFILE


def test_activation_rejects_incomplete_profile_and_lead_cannot_mutate(job_context):
    """Catches publishing unscoreable jobs and read-only leads editing catalog data."""
    from app.core.exceptions import AuthorizationError, ConflictError
    from app.domain.enums import Role
    from app.security.tokens import Principal
    from app.services.jobs import JobService

    session_factory, admin = job_context
    with session_factory() as session:
        service = JobService(session)
        incomplete = service.create_job(admin, "Draft", "Some JD", {})
        with pytest.raises(ConflictError, match="岗位画像不完整"):
            service.activate_job(admin, incomplete.id)

        lead = Principal(user_id="lead", tenant_id=admin.tenant_id, role=Role.LEAD)
        with pytest.raises(AuthorizationError):
            service.update_job(lead, incomplete.id, jd_text="tampered")
