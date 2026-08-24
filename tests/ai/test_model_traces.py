from __future__ import annotations

from pydantic import BaseModel

from app.ai.contracts import ModelRequest, ModelResponse
from app.ai.gateway import ModelGatewayError
from app.database import Base, create_engine_and_session
from app.models.identity import Tenant
from app.models.prompts import PromptVersion
from app.repositories.model_traces import ModelTraceWriter
from app.repositories.prompts import PromptRepository
from app.services.ai_tracing import AITraceSink


class Answer(BaseModel):
    value: str


def _database(tmp_path):
    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'trace.db'}")
    Base.metadata.create_all(engine)
    return session_factory


def _trace_sink(session_factory):
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWorkFactory

    return AITraceSink(SqlAlchemyUnitOfWorkFactory(session_factory))


def _request():
    return ModelRequest(
        operation="resume_extract",
        prompt_version="resume-extract-v1",
        system="private-system-prompt",
        user="candidate-private-text",
        schema=Answer,
    )


def test_trace_contains_metadata_without_prompt_or_resume_text(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        response = ModelResponse(Answer(value="ok"), "fake", "fake-model", 12, 8, 0.001, 25.0)
        trace = ModelTraceWriter(session).succeeded(
            tenant.id,
            "resume_extract",
            "resume-id",
            ["resume-id"],
            _request(),
            response,
        )
        session.commit()
        serialized = " ".join(str(value) for value in vars(trace).values())
        assert "candidate-private-text" not in serialized
        assert "private-system-prompt" not in serialized
        assert trace.operation == "resume_extract"
        assert trace.prompt_version == "resume-extract-v1"
        assert trace.input_tokens == 12
        assert len(trace.request_fingerprint) == 64


def test_failed_trace_records_normalized_fallback_reason(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.flush()
        trace = ModelTraceWriter(session).failed(
            tenant.id,
            "resume_extract",
            "resume-id",
            ["resume-id"],
            _request(),
            ModelGatewayError("timeout", retryable=True),
            20001.0,
        )
        assert trace.status == "failed"
        assert trace.error_code == "timeout"
        assert trace.fallback_reason == "timeout"
        assert trace.attempt_count == 1


def test_trace_sink_persists_rejected_semantic_operation(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        tenant = Tenant(name="Acme")
        session.add(tenant)
        session.commit()
        tenant_id = tenant.id
    response = ModelResponse(Answer(value="ok"), "fake", "fake-model", 12, 8, 0, 5, attempts=2)
    _trace_sink(session_factory).succeeded(
        tenant_id,
        "semantic_match",
        "resume-id",
        ["resume-id", "job-id"],
        _request(),
        response,
        fallback_reason="invalid_semantic_citations",
    )
    from sqlalchemy import select
    from app.models.operations import ModelTrace

    with session_factory() as session:
        trace = session.scalar(select(ModelTrace))
        assert trace.status == "rejected"
        assert trace.fallback_reason == "invalid_semantic_citations"
        assert trace.attempt_count == 2


def test_prompt_repository_returns_only_tenant_active_version(tmp_path):
    session_factory = _database(tmp_path)
    with session_factory() as session:
        acme = Tenant(name="Acme")
        globex = Tenant(name="Globex")
        session.add_all([acme, globex])
        session.flush()
        session.add_all(
            [
                PromptVersion(
                    tenant_id=acme.id,
                    operation="resume_extract",
                    version="v1",
                    schema_version="1",
                    template_fingerprint="a" * 64,
                    is_active=True,
                ),
                PromptVersion(
                    tenant_id=globex.id,
                    operation="resume_extract",
                    version="v2",
                    schema_version="1",
                    template_fingerprint="b" * 64,
                    is_active=True,
                ),
            ]
        )
        session.commit()
        prompt = PromptRepository(session).active(acme.id, "resume_extract")
        assert prompt is not None
        assert prompt.version == "v1"
