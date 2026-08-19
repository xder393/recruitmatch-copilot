from __future__ import annotations


def test_job_versions_are_persisted_as_separate_immutable_rows(tmp_path):
    """Catches replacing version history with an in-place JD update."""
    from app.database import Base, create_engine_and_session
    from app.domain.enums import JobStatus
    from app.models.identity import Tenant
    from app.models.jobs import Job, JobVersion

    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(engine)

    with session_factory() as session:
        tenant = Tenant(name="Acme")
        job = Job(tenant=tenant, title="AI Engineer", status=JobStatus.DRAFT, current_version=2)
        job.versions.extend(
            [
                JobVersion(version=1, jd_text="v1", profile={"skills": ["Python"]}),
                JobVersion(version=2, jd_text="v2", profile={"skills": ["Python", "RAG"]}),
            ]
        )
        session.add(job)
        session.commit()
        job_id = job.id

    with session_factory() as session:
        stored = session.get(Job, job_id)
        assert stored is not None
        assert [item.version for item in stored.versions] == [1, 2]
        assert stored.versions[0].jd_text == "v1"
        assert stored.versions[1].jd_text == "v2"
