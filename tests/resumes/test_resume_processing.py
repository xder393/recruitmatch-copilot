from __future__ import annotations


class _MemoryStore:
    def __init__(self, content: bytes | None = None, error: Exception | None = None):
        self.content = content
        self.error = error

    def read(self, key: str) -> bytes:
        if self.error is not None:
            raise self.error
        return self.content or b""


def _resume_database(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.domain.enums import ResumeStatus
    from app.models import Job, JobTemplate, JobVersion, Resume, ResumeArtifact, Tenant, User  # noqa: F401

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
            artifact=ResumeArtifact(storage_key="tenant/file.txt"),
        )
        session.add(resume)
        session.commit()
        return factory, tenant.id, resume.id


def test_processing_persists_text_profile_and_success_status(tmp_path):
    """Catches a worker reporting success without durable parse output."""
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    service = ResumeProcessingService(
        factory,
        _MemoryStore("使用 Python 和 FastAPI 开发 5 年".encode()),
        HeuristicResumeParser(),
    )
    service.process(tenant_id, resume_id)

    with factory() as session:
        resume = session.get(Resume, resume_id)
        assert resume.status is ResumeStatus.SUCCEEDED
        assert resume.artifact.extracted_text == "使用 Python 和 FastAPI 开发 5 年"
        assert [item["name"] for item in resume.profile["skills"]] == ["Python", "FastAPI"]
        assert resume.profile["experience_years"] == 5


def test_processing_failure_records_stable_error_without_raising(tmp_path):
    """Catches stuck running tasks and unstable storage exception leakage."""
    from app.domain.enums import ResumeStatus
    from app.models.resumes import Resume
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    ResumeProcessingService(factory, _MemoryStore(error=OSError("disk secret")), HeuristicResumeParser()).process(
        tenant_id, resume_id
    )

    with factory() as session:
        resume = session.get(Resume, resume_id)
        assert resume.status is ResumeStatus.FAILED
        assert resume.error_code == "artifact_read_failed"
        assert "disk secret" not in (resume.error_message or "")


def test_traceable_parser_result_is_written_in_same_success_flow(tmp_path):
    from app.ai.resume_parser import LLMResumeParser
    from app.models.operations import ModelTrace
    from app.resumes.parser import HeuristicResumeParser
    from app.services.resume_processing import ResumeProcessingService
    from tests.ai.fakes import FakeStructuredModel

    factory, tenant_id, resume_id = _resume_database(tmp_path)
    parser = LLMResumeParser(
        FakeStructuredModel(
            {
                "resume_extract": {
                    "skills": [
                        {"name": "Python", "evidence": {"start": 0, "end": 6, "text": "Python"}}
                    ]
                }
            }
        ),
        HeuristicResumeParser(),
    )
    ResumeProcessingService(factory, _MemoryStore(b"Python developer"), parser).process(tenant_id, resume_id)

    with factory() as session:
        traces = session.query(ModelTrace).all()
        assert len(traces) == 1
        assert traces[0].business_id == resume_id
