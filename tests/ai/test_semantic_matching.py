from __future__ import annotations

from app.ai.semantic_matching import SemanticMatcher, SemanticProjectScore, validate_semantic_score
from app.retrieval import RetrievedChunk, SearchScope


class Embedder:
    model_name = "fake-512-v1"

    def embed_query(self, text):
        return [1.0] + [0.0] * 511

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


def _scope():
    return SearchScope(
        "tenant",
        frozenset({"resume", "job_version"}),
        frozenset({("resume", "resume", "sha"), ("job_version", "job", "1")}),
    )


def _hit(source_type, source_id, citation_id):
    return RetrievedChunk(
        citation_id,
        "tenant",
        citation_id,
        source_type,
        source_id,
        "sha" if source_type == "resume" else "1",
        1,
        source_type,
        0,
        len(source_type),
        None,
        None,
        1.0,
    )


def test_score_requires_both_source_types():
    score = SemanticProjectScore(
        score=95,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=[],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job_version"}) is None


def test_unknown_citation_rejects_semantic_score():
    score = SemanticProjectScore(
        score=80,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=["invented"],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job_version"}) is None


def test_valid_score_is_clamped_to_percentage_scale():
    score = SemanticProjectScore(
        score=120,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=["j1"],
    )
    validated = validate_semantic_score(score, {"r1": "resume", "j1": "job_version"})
    assert validated is not None
    assert validated.score == 100


def test_swapped_source_citations_are_rejected():
    score = SemanticProjectScore(
        score=90,
        rationale="fit",
        resume_citation_ids=["j1"],
        job_citation_ids=["r1"],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job_version"}) is None


def test_retrieval_failure_returns_no_semantic_score_without_calling_model():
    class BrokenIndex:
        def search(self, *args, **kwargs):
            raise RuntimeError("embedding store unavailable")

    class NoCallModel:
        def generate(self, request):
            raise AssertionError("model must not run after retrieval failure")

    assert (
        SemanticMatcher(NoCallModel(), BrokenIndex(), Embedder()).score(_scope(), "resume", "job", "candidate", "job")
        is None
    )


def test_trace_storage_failure_cannot_break_semantic_result():
    from app.ai.contracts import ModelResponse

    class Index:
        def search(self, scope, *args, **kwargs):
            source_type, source_id, _ = next(iter(scope.authorized_sources))
            return [_hit(source_type, source_id, source_type)]

        def resolve_active_citations(self, scope, citation_ids):
            return [_hit("resume", "resume", "resume"), _hit("job_version", "job", "job_version")]

    class Model:
        def generate(self, request):
            return ModelResponse(
                value=SemanticProjectScore(
                    score=80,
                    rationale="fit",
                    resume_citation_ids=["resume"],
                    job_citation_ids=["job_version"],
                ),
                provider="fake",
                model="fake",
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0,
                latency_ms=1,
            )

    class BrokenTrace:
        def succeeded(self, *args, **kwargs):
            raise RuntimeError("trace database unavailable")

    result = SemanticMatcher(Model(), Index(), Embedder(), trace_sink=BrokenTrace()).score(
        _scope(), "resume", "job", "Python AI 项目", "招聘 AI 应用开发"
    )
    assert result is not None
    assert result.score == 80


def test_model_citations_are_re_resolved_after_generation():
    from app.ai.contracts import ModelResponse

    class Index:
        def search(self, scope, *args, **kwargs):
            source_type, source_id, _ = next(iter(scope.authorized_sources))
            return [_hit(source_type, source_id, source_type)]

        def resolve_active_citations(self, scope, citation_ids):
            return []

    class Model:
        def generate(self, request):
            return ModelResponse(
                value=SemanticProjectScore(
                    score=80,
                    rationale="fit",
                    resume_citation_ids=["resume"],
                    job_citation_ids=["job_version"],
                ),
                provider="fake",
                model="fake",
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0,
                latency_ms=1,
            )

    assert (
        SemanticMatcher(Model(), Index(), Embedder()).score(
            _scope(), "resume", "job", "Python AI 项目", "招聘 AI 应用开发"
        )
        is None
    )


def test_job_and_resume_queries_select_relevant_chinese_chunks_above_threshold():
    import re

    from app.ai.contracts import ModelResponse
    from tests.fakes.retrieval import FakeRecruitingChunk, FakeRecruitingVectorIndex

    relevant = [1.0] + [0.0] * 511
    irrelevant = [0.0, 1.0] + [0.0] * 510
    index = FakeRecruitingVectorIndex(
        [
            FakeRecruitingChunk(
                "rr",
                "tenant",
                "resume-rag",
                "resume",
                "resume",
                "sha",
                1,
                1,
                relevant,
                "directional",
                True,
                "主导中文 RAG 招聘系统项目",
            ),
            FakeRecruitingChunk(
                "ri",
                "tenant",
                "resume-sales",
                "resume",
                "resume",
                "sha",
                1,
                1,
                irrelevant,
                "directional",
                True,
                "线下销售经历",
            ),
            FakeRecruitingChunk(
                "jr",
                "tenant",
                "job-rag",
                "job_version",
                "job",
                "1",
                1,
                1,
                relevant,
                "directional",
                True,
                "负责 RAG 与向量检索",
            ),
            FakeRecruitingChunk(
                "ji",
                "tenant",
                "job-java",
                "job_version",
                "job",
                "1",
                1,
                1,
                irrelevant,
                "directional",
                True,
                "维护 Java 账务系统",
            ),
        ]
    )

    class DirectionalEmbedder:
        model_name = "directional"

        def embed_query(self, text):
            return relevant if "RAG" in text else irrelevant

    class Model:
        request = None

        def generate(self, request):
            self.request = request
            ids = re.findall(r"\[citation:([^\]]+)\]", request.user)
            return ModelResponse(
                value=SemanticProjectScore(
                    score=80,
                    rationale="相关",
                    resume_citation_ids=[ids[0]],
                    job_citation_ids=[ids[-1]],
                ),
                provider="fake",
                model="fake",
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0,
                latency_ms=1,
            )

    model = Model()
    result = SemanticMatcher(model, index, DirectionalEmbedder(), top_k=1, min_score=0.5).score(
        _scope(), "resume", "job", "项目：中文 RAG 招聘系统", "岗位：负责 RAG 与向量检索"
    )

    assert result is not None
    assert result.resume_citation_ids == ["resume-rag"]
    assert result.job_citation_ids == ["job-rag"]
    assert "线下销售" not in model.request.user
    assert "Java 账务" not in model.request.user
