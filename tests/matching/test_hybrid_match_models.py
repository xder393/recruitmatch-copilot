from __future__ import annotations

from app.database import Base, create_engine_and_session
from app.domain.enums import JobStatus, MatchStatus, ResumeStatus
from app.models import Job, JobVersion, MatchResult, MatchRun, Resume, Tenant


def _entities(session):
    tenant = Tenant(name="Acme")
    session.add(tenant)
    session.flush()
    job = Job(tenant=tenant, title="AI Engineer", status=JobStatus.ACTIVE, current_version=1)
    version = JobVersion(job=job, version=1, jd_text="Python RAG", profile={})
    resume = Resume(
        tenant_id=tenant.id,
        sha256="abc",
        original_filename="resume.txt",
        media_type="text/plain",
        size_bytes=1,
        status=ResumeStatus.SUCCEEDED,
    )
    run = MatchRun(tenant_id=tenant.id, resume=resume, status=MatchStatus.SUCCEEDED)
    session.add_all([job, version, resume, run])
    session.flush()
    return run, version


def test_rules_result_allows_empty_ai_fields(tmp_path):
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'rules.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        run, version = _entities(session)
        result = MatchResult(
            run=run,
            job_version=version,
            rank=1,
            total_score=0.8,
            dimension_scores={},
        )
        session.add(result)
        session.commit()
        session.refresh(result)
        assert result.rule_score is None
        assert result.semantic_score is None
        assert result.citations == []


def test_hybrid_components_round_trip(tmp_path):
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'hybrid.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        run, version = _entities(session)
        result = MatchResult(
            run=run,
            job_version=version,
            rank=1,
            rule_score=0.75,
            semantic_score=0.9,
            total_score=0.78,
            dimension_scores={},
            grounding_status="grounded",
            fallback_reason=None,
            citations=[{"id": "c1", "source_type": "resume"}],
        )
        session.add(result)
        session.commit()
        session.refresh(result)
        assert result.total_score == 0.78
        assert result.citations[0]["id"] == "c1"
