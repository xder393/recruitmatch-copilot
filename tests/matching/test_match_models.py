from __future__ import annotations


def test_feedback_appends_without_changing_historical_job_version(tmp_path):
    """Catches recruiter feedback rewriting the recommendation being audited."""
    from app.database import Base, create_engine_and_session
    from app.domain.enums import FeedbackAction, JobStatus, MatchStatus, ResumeStatus, Role
    from app.models import (
        Feedback,
        Job,
        JobTemplate,
        JobVersion,
        MatchResult,
        MatchRun,
        Resume,
        ResumeArtifact,
        Tenant,
        User,
    )

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'matching.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        user = User(tenant=tenant, email="admin@acme.test", password_hash="x", role=Role.ADMIN)
        job = Job(tenant=tenant, title="AI Engineer", status=JobStatus.ACTIVE, current_version=1)
        version = JobVersion(job=job, version=1, jd_text="Python", profile={})
        resume = Resume(
            tenant_id=tenant.id,
            sha256="abc",
            original_filename="a.txt",
            media_type="text/plain",
            size_bytes=1,
            status=ResumeStatus.SUCCEEDED,
        )
        run = MatchRun(tenant_id=tenant.id, resume=resume, status=MatchStatus.SUCCEEDED)
        result = MatchResult(
            run=run,
            job_version=version,
            rank=1,
            total_score=0.9,
            dimension_scores={},
            evidence=[],
            matched_items=[],
            missing_items=[],
            uncertain_items=[],
            risk_flags=[],
        )
        result.feedback_entries.extend(
            [
                Feedback(tenant_id=tenant.id, user=user, action=FeedbackAction.CONFIRM),
                Feedback(tenant_id=tenant.id, user=user, action=FeedbackAction.REJECT, reason="later review"),
            ]
        )
        session.add_all([tenant, user, job, version, resume, run, result])
        session.commit()

        assert len(result.feedback_entries) == 2
        assert result.job_version_id == version.id
