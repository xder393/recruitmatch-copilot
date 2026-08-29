"""Real-PostgreSQL barriers for the Resume privacy/matching lock protocol."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re
from threading import Event, Lock
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.contracts import ModelResponse
from app.ai.explanations import GroundedClaim, GroundedModelOutput
from app.ai.semantic_matching import SemanticProjectScore
from app.domain.enums import ResumeStatus
from app.main import create_app
from app.models.matching import MatchResult, MatchRun
from app.models.resumes import Resume
from app.models.retrieval import RecruitingChunk
from app.repositories.resumes import ResumeRepository
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence
from app.retrieval.ports import SearchScope
from tests.integration.test_retrieval_composition import Fake512Embedder, _settings


class BlockingSemanticModel:
    def __init__(self, block_operation: str = "semantic_project_match") -> None:
        self.semantic_started = Event()
        self.release_semantic = Event()
        self.block_operation = block_operation
        self.forced_explanation_citation: str | None = None
        self._blocked = False
        self._lock = Lock()

    def generate(self, request):
        if request.operation == self.block_operation:
            with self._lock:
                should_block = not self._blocked
                self._blocked = True
            if should_block:
                self.semantic_started.set()
                assert self.release_semantic.wait(timeout=10)
        if request.operation == "resume_extract":
            value = ResumeProfile(
                skills=[SkillEvidence(name="Python", evidence=Evidence(start=0, end=6, text="Python"))]
            )
        elif request.operation == "semantic_project_match":
            citation_ids = re.findall(r"\[citation:([^\]]+)\]", request.user)
            value = SemanticProjectScore(
                score=80,
                rationale="active evidence",
                resume_citation_ids=[citation_ids[0]],
                job_citation_ids=[citation_ids[-1]],
            )
        else:
            citation_ids = re.findall(r"\[citation:([^\]]+)\]", request.user)
            value = GroundedModelOutput(
                summary=GroundedClaim(
                    text="grounded",
                    citation_ids=[self.forced_explanation_citation]
                    if self.forced_explanation_citation
                    else citation_ids[:1],
                )
            )
        return ModelResponse(
            value=value,
            provider="fake",
            model="blocking",
            input_tokens=1,
            output_tokens=1,
            estimated_cost=0,
            latency_ms=1,
        )


def _seed(client: TestClient, suffix: str = "") -> tuple[str, str, str]:
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "tenant_name": f"Privacy Tenant {suffix}",
            "email": f"privacy{suffix}@recruitmatch.test",
            "password": "correct horse battery staple",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": f"privacy{suffix}@recruitmatch.test",
            "password": "correct horse battery staple",
        },
    )
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    tenant_id = client.get("/api/v1/auth/me").json()["tenant_id"]
    resume = client.post(
        "/api/v1/resumes",
        files={"file": (f"candidate{suffix}.txt", f"Python FastAPI {suffix}".encode(), "text/plain")},
    ).json()
    job = client.post(
        "/api/v1/jobs",
        json={
            "title": "AI Application Engineer",
            "jd_text": "Python FastAPI recruiting",
            "profile": {
                "job_family": "ai_application",
                "level": "mid",
                "required_skills": ["Python"],
                "preferred_skills": [],
                "weights": {"skills": 1},
            },
        },
    ).json()
    assert client.post(f"/api/v1/jobs/{job['id']}/activate").status_code == 200
    return tenant_id, resume["id"], job["id"]


def test_delete_waits_for_match_commit_then_scrubs_new_results_and_citations(tmp_path):
    model = BlockingSemanticModel()
    settings = _settings(tmp_path)
    app = create_app(settings, structured_model=model, knowledge_embedder=Fake512Embedder())
    with TestClient(app) as client:
        tenant_id, resume_id, _ = _seed(client)
        auth = dict(client.headers)

        def match():
            with TestClient(app, headers=auth) as concurrent:
                return concurrent.post(f"/api/v1/resumes/{resume_id}/matches?mode=hybrid-v1")

        def delete():
            with TestClient(app, headers=auth) as concurrent:
                return concurrent.delete(f"/api/v1/resumes/{resume_id}")

        with ThreadPoolExecutor(max_workers=2) as executor:
            matching = executor.submit(match)
            assert model.semantic_started.wait(timeout=10)
            deleting = executor.submit(delete)
            time.sleep(0.2)
            assert not deleting.done(), "delete must wait on the matching Resume row lock"
            model.release_semantic.set()
            match_response = matching.result(timeout=10)
            assert match_response.status_code == 201, match_response.text
            citations = frozenset(
                item["id"]
                for item in match_response.json()["results"][0]["citations"]
                if item["source_type"] == "resume"
            )
            assert citations
            assert deleting.result(timeout=10).status_code == 204

        with app.state.session_factory() as session:
            run = session.scalar(select(MatchRun).where(MatchRun.resume_id == resume_id))
            result = session.scalar(select(MatchResult).where(MatchResult.run_id == run.id))
            assert result.evidence == []
            assert result.citations == []
            assert result.grounded_explanation == {}
            assert result.interview_questions == []
        assert app.state.retrieval_index.resolve_historical_citations(tenant_id, citations) == []


def test_match_waits_for_winning_delete_and_then_rejects_deleted_resume(tmp_path):
    model = BlockingSemanticModel()
    model.release_semantic.set()
    settings = _settings(tmp_path)
    app = create_app(settings, structured_model=model, knowledge_embedder=Fake512Embedder())
    with TestClient(app) as client:
        tenant_id, resume_id, _ = _seed(client, "reverse")
        auth = dict(client.headers)
        with app.state.session_factory() as delete_session:
            resume = delete_session.scalar(
                select(Resume).where(Resume.tenant_id == tenant_id, Resume.id == resume_id).with_for_update(of=Resume)
            )
            assert resume is not None and resume.status is ResumeStatus.SUCCEEDED

            with ThreadPoolExecutor(max_workers=1) as executor:
                matching = executor.submit(
                    lambda: TestClient(app, headers=auth).post(f"/api/v1/resumes/{resume_id}/matches?mode=hybrid-v1")
                )
                time.sleep(0.2)
                assert not matching.done(), "match must wait on the deleting Resume row lock"
                ResumeRepository(delete_session).scrub_private_data(tenant_id, resume, datetime.now(timezone.utc))
                delete_session.commit()
                response = matching.result(timeout=10)
                assert response.status_code == 404

        scope = SearchScope(tenant_id, frozenset({"resume"}), frozenset())
        assert app.state.retrieval_index.resolve_active_citations(scope, frozenset()) == []


def test_job_deactivation_during_model_call_forces_semantic_fallback(tmp_path):
    model = BlockingSemanticModel()
    settings = _settings(tmp_path)
    app = create_app(settings, structured_model=model, knowledge_embedder=Fake512Embedder())
    with TestClient(app) as client:
        _, resume_id, job_id = _seed(client, "jobinactive")
        auth = dict(client.headers)
        with ThreadPoolExecutor(max_workers=1) as executor:
            matching = executor.submit(
                lambda: TestClient(app, headers=auth).post(f"/api/v1/resumes/{resume_id}/matches?mode=hybrid-v1")
            )
            assert model.semantic_started.wait(timeout=10)
            assert client.post(f"/api/v1/jobs/{job_id}/deactivate").status_code == 200
            model.release_semantic.set()
            response = matching.result(timeout=10)
        assert response.status_code == 201
        result = response.json()["results"][0]
        assert result["semantic_score"] is None
        assert result["grounding_status"] == "rules_fallback"


def test_knowledge_deactivation_during_explanation_forces_guidance_fallback(tmp_path):
    model = BlockingSemanticModel(block_operation="match_explanation")
    settings = _settings(tmp_path)
    app = create_app(settings, structured_model=model, knowledge_embedder=Fake512Embedder())
    with TestClient(app) as client:
        _, resume_id, _ = _seed(client, "knowledgeinactive")
        knowledge = client.post(
            "/api/v1/knowledge-documents",
            data={"document_type": "policy"},
            files={"file": ("policy.txt", b"Evidence policy", "text/plain")},
        ).json()
        with app.state.session_factory() as session:
            model.forced_explanation_citation = session.scalar(
                select(RecruitingChunk.citation_id).where(
                    RecruitingChunk.source_type == "knowledge_document",
                    RecruitingChunk.source_id == knowledge["id"],
                )
            )
        assert model.forced_explanation_citation
        auth = dict(client.headers)
        with ThreadPoolExecutor(max_workers=1) as executor:
            matching = executor.submit(
                lambda: TestClient(app, headers=auth).post(f"/api/v1/resumes/{resume_id}/matches?mode=hybrid-v1")
            )
            assert model.semantic_started.wait(timeout=10)
            assert client.post(f"/api/v1/knowledge-documents/{knowledge['id']}/deactivate").status_code == 200
            model.release_semantic.set()
            response = matching.result(timeout=10)
        assert response.status_code == 201
        result = response.json()["results"][0]
        assert result["grounding_status"] == "empty_model_output"
        assert model.forced_explanation_citation not in {item["id"] for item in result["citations"]}
