from __future__ import annotations


class _MemoryStore:
    def __init__(self, content: bytes | None = None, error: Exception | None = None):
        self.content = content
        self.error = error

    def read_bounded(self, location, max_bytes: int) -> bytes:
        if self.error is not None:
            raise self.error
        return self.content or b""


def _uow_factory(session_factory):
    from tests.fakes.processing import FakeProcessingUnitOfWorkFactory

    return FakeProcessingUnitOfWorkFactory(session_factory)


def _resume_database(tmp_path, content=b"Python developer"):
    from app.database import Base, create_engine_and_session
    from app.domain.enums import ResumeStatus
    from app.models import Job, JobTemplate, JobVersion, Resume, Tenant, User  # noqa: F401

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'processing.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        resume = Resume(
            tenant_id=tenant.id,
            sha256="abc",
            original_filename="resume.txt",
            media_type="text/plain",
            size_bytes=20,
            status=ResumeStatus.QUEUED,
        )
        session.add(resume)
        from tests.support.artifacts import attach_artifact

        attach_artifact(session, resume, content)
        session.commit()
        return factory, tenant.id, resume.id


def test_processing_persists_text_profile_and_success_status(tmp_path):
    """Catches a worker reporting success without durable parse output."""
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path, "使用 Python 和 FastAPI 开发 5 年".encode())
    service = ResumeProcessingService(
        _uow_factory(factory),
        _MemoryStore("使用 Python 和 FastAPI 开发 5 年".encode()),
        HeuristicResumeParser(),
    )
    service.process(tenant_id, resume_id)

    with factory() as session:
        resume = session.get(Resume, resume_id)
        assert resume.status is ResumeStatus.SUCCEEDED
        assert resume.extracted_text == "使用 Python 和 FastAPI 开发 5 年"
        assert [item["name"] for item in resume.profile["skills"]] == ["Python", "FastAPI"]
        assert resume.profile["experience_years"] == 5


def test_processing_failure_records_stable_error_without_raising(tmp_path):
    """Catches stuck running tasks and unstable storage exception leakage."""
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    ResumeProcessingService(
        _uow_factory(factory), _MemoryStore(error=OSError("disk secret")), HeuristicResumeParser()
    ).process(tenant_id, resume_id)

    with factory() as session:
        resume = session.get(Resume, resume_id)
        assert resume.status is ResumeStatus.FAILED
        assert resume.error_code == "storage_unavailable"
        assert "disk secret" not in (resume.error_message or "")


def test_duplicate_worker_delivery_does_not_reprocess_succeeded_resume(tmp_path):
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    ResumeProcessingService(
        _uow_factory(factory),
        _MemoryStore(b"Python developer"),
        HeuristicResumeParser(),
    ).process(tenant_id, resume_id)
    ResumeProcessingService(
        _uow_factory(factory),
        _MemoryStore(error=OSError("must not read twice")),
        HeuristicResumeParser(),
    ).process(tenant_id, resume_id)

    with factory() as session:
        assert session.get(Resume, resume_id).status is ResumeStatus.SUCCEEDED


def test_running_worker_duplicate_is_acked_then_expired_work_is_recovered(tmp_path):
    from datetime import datetime, timedelta, timezone

    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    with factory() as session:
        resume = session.get(Resume, resume_id)
        resume.status = ResumeStatus.RUNNING
        resume.processing_lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        session.commit()
    service = ResumeProcessingService(_uow_factory(factory), _MemoryStore(b"Python developer"), HeuristicResumeParser())
    assert service.process(tenant_id, resume_id).value == "duplicate_active"

    with factory() as session:
        resume = session.get(Resume, resume_id)
        resume.processing_lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()
    assert service.process(tenant_id, resume_id).value == "completed"
    with factory() as session:
        assert session.get(Resume, resume_id).status is ResumeStatus.SUCCEEDED


def test_traceable_parser_result_is_written_in_same_success_flow(tmp_path):
    from app.ai.resume_parser import LLMResumeParser
    from app.models.operations import ModelTrace
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService
    from tests.ai.fakes import FakeStructuredModel

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    parser = LLMResumeParser(
        FakeStructuredModel(
            {"resume_extract": {"skills": [{"name": "Python", "evidence": {"start": 0, "end": 6, "text": "Python"}}]}}
        ),
        HeuristicResumeParser(),
    )
    ResumeProcessingService(_uow_factory(factory), _MemoryStore(b"Python developer"), parser).process(
        tenant_id, resume_id
    )

    with factory() as session:
        traces = session.query(ModelTrace).all()
        assert len(traces) == 1
        assert traces[0].business_id == resume_id


def test_recovery_delivery_infers_index_only_from_durable_parsed_text(tmp_path):
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    ResumeProcessingService(_uow_factory(factory), _MemoryStore(b"Python developer"), HeuristicResumeParser()).process(
        tenant_id, resume_id
    )
    with factory() as session:
        row = session.get(Resume, resume_id)
        expected_profile = {**row.profile, "reviewed_marker": "preserve"}
        row.profile = expected_profile
        row.status = ResumeStatus.QUEUED
        session.commit()

    class ForbiddenParser:
        def parse(self, text):
            raise AssertionError("index repair must not parse again")

    result = ResumeProcessingService(
        _uow_factory(factory), _MemoryStore(error=AssertionError("must not read again")), ForbiddenParser()
    ).process(tenant_id, resume_id)
    assert result.value == "completed"
    with factory() as session:
        row = session.get(Resume, resume_id)
        assert row.status == ResumeStatus.SUCCEEDED
        assert row.profile == expected_profile
        assert row.extracted_text == "Python developer"
        assert row.processing_lease_epoch == 2


def test_inconsistent_parsed_pair_fails_closed_without_reprocessing(tmp_path):
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    with factory() as session:
        session.get(Resume, resume_id).extracted_text = "orphan text without profile"
        session.commit()
    ResumeProcessingService(_uow_factory(factory), _MemoryStore(b"Python developer"), HeuristicResumeParser()).process(
        tenant_id, resume_id
    )
    with factory() as session:
        row = session.get(Resume, resume_id)
        assert row.status == ResumeStatus.FAILED
        assert row.error_code == "resume_parsed_state_invalid"
        assert row.profile == {} and row.extracted_text == "orphan text without profile"
