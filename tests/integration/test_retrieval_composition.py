from __future__ import annotations

import os
import re

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

from app.config import Settings
from app.ai.contracts import ModelResponse
from app.ai.explanations import GroundedClaim, GroundedModelOutput
from app.ai.semantic_matching import SemanticProjectScore
from app.main import create_app
from app.models.retrieval import RecruitingChunk
from app.retrieval.generations import GenerationWriter
from app.retrieval.indexing import SourceIndexer
from app.retrieval.pgvector_index import PgVectorRecruitingIndex
from app.tasks.celery_app import build_worker_dependencies
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence


class Fake512Embedder:
    model_name = "fake-composition-512-v1"

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        vector = [0.0] * 512
        vector[sum(text.encode()) % 512] = 1.0
        return vector


class CompositionModel:
    def generate(self, request):
        if request.operation == "resume_extract":
            value = ResumeProfile(
                skills=[SkillEvidence(name="Python", evidence=Evidence(start=0, end=6, text="Python"))]
            )
        elif request.operation == "semantic_project_match":
            citation_ids = re.findall(r"\[citation:([^\]]+)\]", request.user)
            value = SemanticProjectScore(
                score=80,
                rationale="authorized evidence",
                resume_citation_ids=[citation_ids[0]],
                job_citation_ids=[citation_ids[-1]],
            )
        else:
            citation_ids = re.findall(r"\[citation:([^\]]+)\]", request.user)
            value = GroundedModelOutput(summary=GroundedClaim(text="grounded", citation_ids=citation_ids[:1]))
        return ModelResponse(
            value=value,
            provider="fake",
            model="composition",
            input_tokens=1,
            output_tokens=1,
            estimated_cost=0,
            latency_ms=1,
        )


def _settings(tmp_path):
    return Settings(
        api_key="test-key",
        database_url=os.environ["DATABASE_URL"],
        jwt_secret="integration-secret-that-is-at-least-32-bytes",
        task_mode="inline",
        ai_enabled=True,
        retrieval_min_score=0.0,
    )


@pytest.fixture
def composition_settings(tmp_path):
    settings = _settings(tmp_path)
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE tenants CASCADE"))
    engine.dispose()
    yield settings
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE tenants CASCADE"))
    engine.dispose()


def test_fastapi_and_worker_use_real_pgvector_generation_composition(composition_settings):
    settings = composition_settings
    embedder = Fake512Embedder()
    app = create_app(settings, structured_model=CompositionModel(), knowledge_embedder=embedder)

    with TestClient(app) as client:
        from app.observability.adapters import ObservedVectorIndex

        assert isinstance(app.state.retrieval_index, ObservedVectorIndex)
        assert isinstance(app.state.retrieval_index.index, PgVectorRecruitingIndex)
        assert isinstance(app.state.source_indexer, SourceIndexer)
        assert isinstance(app.state.source_indexer.writer, GenerationWriter)
        created = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "tenant_name": "Composition Tenant",
                "email": "composition@recruitmatch.test",
                "password": "correct horse battery staple",
            },
        )
        if created.status_code not in {201, 409}:
            raise AssertionError(created.text)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "composition@recruitmatch.test", "password": "correct horse battery staple"},
        )
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        resume = client.post(
            "/api/v1/resumes",
            files={"file": ("candidate.txt", b"Python FastAPI recruiting", "text/plain")},
        )
        assert resume.status_code in {200, 202}
        job = client.post(
            "/api/v1/jobs",
            json={
                "title": "AI Application Engineer",
                "jd_text": "Python FastAPI recruiting",
                "profile": {
                    "job_family": "ai_application",
                    "level": "mid",
                    "required_skills": ["Python", "FastAPI"],
                    "preferred_skills": [],
                    "weights": {"skills": 1},
                },
            },
        )
        assert job.status_code == 201
        assert client.post(f"/api/v1/jobs/{job.json()['id']}/activate").status_code == 200
        knowledge = client.post(
            "/api/v1/knowledge-documents",
            data={"document_type": "policy"},
            files={"file": ("policy.txt", b"Use structured evidence", "text/plain")},
        )
        assert knowledge.status_code == 202

        rules = client.post(f"/api/v1/resumes/{resume.json()['id']}/matches?mode=rules-v1")
        hybrid = client.post(f"/api/v1/resumes/{resume.json()['id']}/matches?mode=hybrid-v1")
        assert rules.status_code == 201
        assert hybrid.status_code == 201
        assert rules.json()["algorithm_version"] == "rules-v1"
        assert hybrid.json()["algorithm_version"] == "hybrid-v1"
        assert hybrid.json()["results"][0]["semantic_score"] == 80
        with app.state.session_factory() as session:
            chunks = session.query(RecruitingChunk).all()
            assert {chunk.source_type for chunk in chunks} == {
                "resume",
                "job_version",
                "knowledge_document",
            }
            assert all(chunk.is_active for chunk in chunks)

    worker = build_worker_dependencies(settings, embedder)
    assert isinstance(worker.retrieval_index, PgVectorRecruitingIndex)
    assert isinstance(worker.source_indexer, SourceIndexer)
    assert isinstance(worker.source_indexer.writer, GenerationWriter)
    assert worker.resume_processor.source_indexer is worker.source_indexer
    assert worker.knowledge_processor.source_indexer is worker.source_indexer
