from __future__ import annotations

import pytest


def _retrieved_chunk(citation_id, source_type, source_id, source_version="1", tenant_id="tenant-1"):
    from app.retrieval import RetrievedChunk

    return RetrievedChunk(
        id=citation_id,
        tenant_id=tenant_id,
        citation_id=citation_id,
        source_type=source_type,
        source_id=source_id,
        source_version=source_version,
        generation=1,
        content=f"evidence for {citation_id}",
        start_offset=0,
        end_offset=10,
        page_number=None,
        section=None,
        score=1.0,
    )


def _hybrid_recommendation(job_id="job-1", job_version_id="job-version-1", *, rule_score=0.5):
    from app.matching.hybrid import combine_scores
    from app.matching.schemas import HybridMatchRecommendation

    return HybridMatchRecommendation(
        job_id=job_id,
        job_version_id=job_version_id,
        title="AI",
        total_score=combine_scores(rule_score, 80),
        dimension_scores={"skills": rule_score, "experience": 0, "projects": 0},
        matched_items=["Python"],
        missing_items=[],
        uncertain_items=[],
        evidence=[],
        risk_flags=[],
        summary="rule summary",
        rule_score=rule_score,
        semantic_score=80,
        grounding_status="grounded",
        citations=["resume-cite", "job-cite"],
    )


class _Embedder:
    model_name = "fake"

    @staticmethod
    def embed_query(text):
        return [1.0]


class _ExplanationService:
    prompt_version = "explanation-v1"

    def __init__(self, citation_id="resume-cite"):
        self.citation_id = citation_id

    def generate(self, scope, resume_id, job_version_id, rule_result, hits):
        from app.ai.explanations import Citation, GroundedClaim, GroundedExplanation

        hit = next(item for item in hits if item.citation_id == self.citation_id)
        return GroundedExplanation(
            summary=GroundedClaim(text="grounded summary", citation_ids=[self.citation_id]),
            citations={
                self.citation_id: Citation(
                    source_type=hit.source_type,
                    source_id=hit.source_id,
                    content=hit.content,
                    start_offset=hit.start_offset,
                    end_offset=hit.end_offset,
                    page_number=hit.page_number,
                )
            },
            grounding_status="grounded",
        )


class _FinalResolutionIndex:
    def __init__(self, *, missing=frozenset(), fail_search=False, fail_resolve=False):
        self.missing = missing
        self.fail_search = fail_search
        self.fail_resolve = fail_resolve

    @staticmethod
    def _source(scope, source_type):
        return next(
            (source_id, source_version)
            for kind, source_id, source_version in scope.authorized_sources
            if kind == source_type
        )

    def search(self, scope, query_embedding, embedding_model, top_k, min_score):
        if self.fail_search:
            raise RuntimeError("guidance search unavailable")
        resume_id, resume_version = self._source(scope, "resume")
        return [_retrieved_chunk("resume-cite", "resume", resume_id, resume_version, scope.tenant_id)]

    def resolve_active_citations(self, scope, citation_ids):
        if self.fail_resolve:
            raise RuntimeError("citation resolver unavailable")
        sources = {"resume-cite": ("resume", *self._source(scope, "resume"))}
        sources["job-cite"] = ("job_version", *self._source(scope, "job_version"))
        return [
            _retrieved_chunk(citation_id, *sources[citation_id], tenant_id=scope.tenant_id)
            for citation_id in sorted(citation_ids - self.missing)
            if citation_id in sources
        ]


class _HybridEngine:
    semantic_matcher = type("Semantic", (), {"prompt_version": "semantic-v1"})()

    def rank(self, profile, candidates, tenant_context, top_k):
        candidate = candidates[0]
        return [_hybrid_recommendation(candidate.job_id, candidate.job_version_id)]


def _setup(tmp_path):
    from app.database import Base, create_engine_and_session
    from app.domain.enums import JobStatus, ResumeStatus, Role
    from app.models import Job, JobVersion, Resume, Tenant, User
    from app.resumes.parser import HeuristicResumeParser
    from app.security.tokens import Principal

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'service.db'}")
    Base.metadata.create_all(engine)
    with factory() as session:
        acme, globex = Tenant(name="Acme"), Tenant(name="Globex")
        session.add_all([acme, globex])
        session.flush()
        admin = User(tenant=acme, email="admin@acme.test", password_hash="x", role=Role.ADMIN)
        session.add(admin)
        session.flush()
        text = "5 年 Python FastAPI RAG 项目经验"
        resume = Resume(
            tenant_id=acme.id,
            uploaded_by=admin.id,
            sha256="abc",
            original_filename="a.txt",
            media_type="text/plain",
            size_bytes=len(text.encode()),
            status=ResumeStatus.SUCCEEDED,
            profile=HeuristicResumeParser().parse(text).model_dump(mode="json"),
            extracted_text=text,
        )
        definitions = [
            ("AI", JobStatus.ACTIVE, ["Python", "FastAPI"], ["RAG"]),
            ("Backend", JobStatus.ACTIVE, ["Python", "MySQL"], ["Redis"]),
            ("Data", JobStatus.ACTIVE, ["Python", "SQL"], ["Spark"]),
            ("Frontend", JobStatus.ACTIVE, ["JavaScript", "React"], ["TypeScript"]),
            ("Inactive perfect", JobStatus.INACTIVE, ["Python"], ["RAG"]),
        ]
        for title, status, required, preferred in definitions:
            job = Job(tenant=acme, title=title, status=status, current_version=1)
            job.versions.append(
                JobVersion(
                    version=1,
                    jd_text=title,
                    profile={
                        "required_skills": required,
                        "preferred_skills": preferred,
                        "min_experience_years": 3,
                        "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
                    },
                    created_by=admin.id,
                )
            )
            session.add(job)
        foreign = Job(tenant=globex, title="Foreign perfect", status=JobStatus.ACTIVE, current_version=1)
        foreign.versions.append(JobVersion(version=1, jd_text="Python RAG", profile={"required_skills": ["Python"]}))
        session.add_all([resume, foreign])
        session.commit()
        principal = Principal(user_id=admin.id, tenant_id=acme.id, role=Role.ADMIN)
        foreign_principal = Principal(user_id="globex", tenant_id=globex.id, role=Role.ADMIN)
        return factory, principal, foreign_principal, resume.id


def test_run_persists_top_three_from_active_same_tenant_jobs(tmp_path):
    """Catches inactive or foreign jobs entering candidate recommendations."""
    from app.domain.enums import MatchStatus
    from app.services.matching import MatchingService
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    factory, principal, _, resume_id = _setup(tmp_path)
    with factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        run = MatchingService(uow.matching, uow=uow).run(principal, resume_id)
        assert run.status is MatchStatus.SUCCEEDED
        assert run.algorithm_version == "rules-v1"
        assert [result.rank for result in run.results] == [1, 2, 3]
        titles = [result.job_version.job.title for result in run.results]
        assert titles == ["AI", "Backend", "Data"]
        assert "Inactive perfect" not in titles
        assert "Foreign perfect" not in titles


def test_final_top_three_promotes_former_fourth_after_semantic_invalidation(tmp_path):
    """Catches truncating the eligible pool before final evidence changes scores."""
    from types import SimpleNamespace

    from sqlalchemy import select
    from app.matching.hybrid import HybridMatchingEngine
    from app.matching.engine import MatchingEngine
    from app.models.matching import MatchResult
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
    from app.services.matching import MatchingService

    # Real rules pipeline with controlled rule scores isolates the final ranking boundary.
    scores = {"AI": 0.90, "Backend": 0.88, "Data": 0.86, "Frontend": 0.84}

    class Rules(MatchingEngine):
        def rank(self, profile, jobs, top_k=3):
            results = super().rank(profile, jobs, top_k=len(jobs))
            results = [item.model_copy(update={"total_score": scores[item.title]}) for item in results]
            return sorted(results, key=lambda item: (-item.total_score, item.job_id))[:top_k]

    class FakeSemantic:
        prompt_version = "fake-final-top3"

        def __init__(self):
            self.calls = []

        def score(self, scope, resume_id, version_id, resume_summary, jd_text):
            self.calls.append(jd_text)
            return SimpleNamespace(score=80, resume_citation_ids=["resume-cite"], job_citation_ids=["job-cite"])

    class Explanations(_ExplanationService):
        def __init__(self):
            super().__init__()
            self.calls = []

        def generate(self, scope, resume_id, job_version_id, rule_result, hits):
            self.calls.append(job_version_id)
            return super().generate(scope, resume_id, job_version_id, rule_result, hits)

    class InvalidateFirst(_FinalResolutionIndex):
        def __init__(self, invalid_version):
            super().__init__()
            self.invalid_version = invalid_version
            self.resolutions = []

        def resolve_active_citations(self, scope, citation_ids):
            version_id, _ = self._source(scope, "job_version")
            self.resolutions.append(version_id)
            hits = super().resolve_active_citations(scope, citation_ids)
            return [hit for hit in hits if version_id != self.invalid_version or hit.citation_id != "job-cite"]

    factory, principal, _, resume_id = _setup(tmp_path)
    semantic, explanations = FakeSemantic(), Explanations()
    with factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        jobs = uow.matching.active_jobs(principal.tenant_id)
        invalid_version = next(job.versions[0].id for job in jobs if job.title == "AI")
        index = InvalidateFirst(invalid_version)
        run = MatchingService(
            uow.matching,
            hybrid_engine=HybridMatchingEngine(Rules(), semantic),
            explanation_service=explanations,
            source_index=index,
            embedder=_Embedder(),
            uow=uow,
        ).run(principal, resume_id, mode="hybrid-v1")
        assert [(result.job_version.job.title, result.total_score) for result in run.results] == [
            ("Backend", 0.864),
            ("Data", 0.848),
            ("Frontend", 0.832),
        ]
        assert [result.rank for result in run.results] == [1, 2, 3]
        assert all(result.semantic_score == 80 for result in run.results)
        assert len(list(session.scalars(select(MatchResult).where(MatchResult.run_id == run.id)))) == 3
        assert semantic.calls == ["AI", "Backend", "Data", "Frontend"]
        assert len(explanations.calls) == len(index.resolutions) == 4
        assert set(explanations.calls) == {job.versions[0].id for job in jobs}


def test_run_hides_resume_from_another_tenant(tmp_path):
    """Catches caller-controlled resume IDs bypassing tenant predicates."""
    from app.core.exceptions import ResourceNotFoundError
    from app.services.matching import MatchingService
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork

    factory, _, foreign_principal, resume_id = _setup(tmp_path)
    with factory() as session:
        with pytest.raises(ResourceNotFoundError):
            uow = SqlAlchemyUnitOfWork(session)
            MatchingService(uow.matching, uow=uow).run(foreign_principal, resume_id)


def test_scope_keeps_old_knowledge_generation_authorized_during_refresh():
    from types import SimpleNamespace

    from app.services.matching import MatchingService

    documents = [
        SimpleNamespace(id="knowledge-1", checksum="checksum", status="processing", search_index_status="ready"),
        SimpleNamespace(id="knowledge-2", checksum="checksum-2", status="inactive", search_index_status="ready"),
    ]
    uow = SimpleNamespace(knowledge=SimpleNamespace(list_documents=lambda tenant_id: documents))
    service = MatchingService(SimpleNamespace(), uow=uow)
    resume = SimpleNamespace(id="resume-1", sha256="sha")
    candidate = SimpleNamespace(job_version_id="job-version-1")

    scope = service._search_scope("tenant-1", resume, [candidate], {"job-version-1": "1"})

    assert ("knowledge_document", "knowledge-1", "checksum") in scope.authorized_sources
    assert ("knowledge_document", "knowledge-2", "checksum-2") not in scope.authorized_sources


def test_run_persists_semantic_citations_excluded_from_guidance_top_k(tmp_path):
    """Catches guidance top-k silently dropping one side of persisted semantic evidence."""
    from app.repositories.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
    from app.services.matching import MatchingService

    factory, principal, _, resume_id = _setup(tmp_path)
    with factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        run = MatchingService(
            uow.matching,
            hybrid_engine=_HybridEngine(),
            explanation_service=_ExplanationService(),
            source_index=_FinalResolutionIndex(),
            embedder=_Embedder(),
            uow=uow,
        ).run(principal, resume_id, mode="hybrid-v1")

        result = run.results[0]
        assert result.semantic_score == 80
        assert result.grounding_status == "grounded"
        assert {citation["id"] for citation in result.citations} == {"resume-cite", "job-cite"}
        assert {citation["source_type"] for citation in result.citations} == {"resume", "job_version"}


def test_final_resolution_invalidation_removes_semantic_contribution_and_absent_claims():
    """Catches a stale semantic side retaining score or claims without persisted payloads."""
    from types import SimpleNamespace

    from app.services.matching import MatchingService
    from app.retrieval import SearchScope

    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version"}),
        frozenset({("resume", "resume-1", "1"), ("job_version", "job-version-1", "1")}),
    )
    service = MatchingService(
        SimpleNamespace(),
        explanation_service=_ExplanationService(),
        source_index=_FinalResolutionIndex(missing=frozenset({"job-cite"})),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )

    [result] = service._add_grounded_guidance(
        scope,
        "resume-1",
        [SimpleNamespace(job_version_id="job-version-1", jd_text="AI")],
        [_hybrid_recommendation()],
    )

    assert result.semantic_score is None
    assert result.total_score == 0.4
    assert result.grounding_status == "rules_fallback"
    assert result.fallback_reason == "invalid_semantic_citations"
    assert {citation["id"] for citation in result.citations} == {"resume-cite"}
    assert result.grounded_explanation["summary"]["citation_ids"] == ["resume-cite"]


def test_final_resolution_removes_guidance_claim_when_its_payload_is_absent():
    """Catches a final-resolution race leaving a claim that references no persisted payload."""
    from types import SimpleNamespace

    from app.services.matching import MatchingService
    from app.retrieval import SearchScope

    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version"}),
        frozenset({("resume", "resume-1", "1"), ("job_version", "job-version-1", "1")}),
    )
    service = MatchingService(
        SimpleNamespace(),
        explanation_service=_ExplanationService(),
        source_index=_FinalResolutionIndex(missing=frozenset({"resume-cite"})),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )

    [result] = service._add_grounded_guidance(
        scope,
        "resume-1",
        [SimpleNamespace(job_version_id="job-version-1", jd_text="AI")],
        [_hybrid_recommendation()],
    )

    assert result.summary == "rule summary"
    assert result.citations == []
    assert result.grounded_explanation["summary"] is None
    assert result.grounded_explanation["grounding_status"] == "empty_model_output"
    assert result.interview_questions == []


def test_final_resolution_failure_degrades_without_unresolved_citations():
    """Catches resolver outages persisting unverifiable semantic or guidance evidence."""
    from types import SimpleNamespace

    from app.services.matching import MatchingService
    from app.retrieval import SearchScope

    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version"}),
        frozenset({("resume", "resume-1", "1"), ("job_version", "job-version-1", "1")}),
    )
    service = MatchingService(
        SimpleNamespace(),
        explanation_service=_ExplanationService(),
        source_index=_FinalResolutionIndex(fail_resolve=True),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )

    [result] = service._add_grounded_guidance(
        scope,
        "resume-1",
        [SimpleNamespace(job_version_id="job-version-1", jd_text="AI")],
        [_hybrid_recommendation()],
    )

    assert result.semantic_score is None
    assert result.total_score == 0.4
    assert result.citations == []
    assert result.grounded_explanation == {}
    assert result.interview_questions == []
    assert result.grounding_status == "rules_fallback"
    assert result.fallback_reason == "guidance_retrieval_unavailable"


def test_guidance_search_failure_still_persists_resolved_semantic_evidence():
    """Catches guidance failure bypassing final semantic citation persistence."""
    from types import SimpleNamespace

    from app.services.matching import MatchingService
    from app.retrieval import SearchScope

    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version"}),
        frozenset({("resume", "resume-1", "1"), ("job_version", "job-version-1", "1")}),
    )
    service = MatchingService(
        SimpleNamespace(),
        explanation_service=_ExplanationService(),
        source_index=_FinalResolutionIndex(fail_search=True),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )

    [result] = service._add_grounded_guidance(
        scope,
        "resume-1",
        [SimpleNamespace(job_version_id="job-version-1", jd_text="AI")],
        [_hybrid_recommendation()],
    )

    assert result.semantic_score == 80
    assert {citation["id"] for citation in result.citations} == {"resume-cite", "job-cite"}
    assert result.grounded_explanation == {}
    assert result.interview_questions == []
    assert result.grounding_status == "rules_fallback"
    assert result.fallback_reason == "guidance_retrieval_unavailable"


def test_final_semantic_invalidation_reranks_results_deterministically():
    """Catches post-resolution score changes retaining stale recommendation order."""
    from types import SimpleNamespace

    from app.services.matching import MatchingService
    from app.retrieval import SearchScope

    class SelectiveInvalidationIndex(_FinalResolutionIndex):
        def resolve_active_citations(self, scope, citation_ids):
            resolved = super().resolve_active_citations(scope, citation_ids)
            if any(source_id == "job-version-b" for _, source_id, _ in scope.authorized_sources):
                return [hit for hit in resolved if hit.citation_id != "job-cite"]
            return resolved

    scope = SearchScope(
        "tenant-1",
        frozenset({"resume", "job_version"}),
        frozenset(
            {
                ("resume", "resume-1", "1"),
                ("job_version", "job-version-a", "1"),
                ("job_version", "job-version-b", "1"),
            }
        ),
    )
    service = MatchingService(
        SimpleNamespace(),
        explanation_service=_ExplanationService(),
        source_index=SelectiveInvalidationIndex(),
        embedder=_Embedder(),
        uow=SimpleNamespace(),
    )
    candidates = [
        SimpleNamespace(job_version_id="job-version-b", jd_text="B"),
        SimpleNamespace(job_version_id="job-version-a", jd_text="A"),
    ]
    recommendations = [
        _hybrid_recommendation("job-b", "job-version-b", rule_score=0.9),
        _hybrid_recommendation("job-a", "job-version-a", rule_score=0.85),
    ]

    results = service._add_grounded_guidance(scope, "resume-1", candidates, recommendations)

    assert [(item.job_id, item.total_score) for item in results] == [("job-a", 0.84), ("job-b", 0.72)]
