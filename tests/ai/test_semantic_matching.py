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
        score=0.95,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=[],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job_version"}) is None


def test_unknown_citation_rejects_semantic_score():
    score = SemanticProjectScore(
        score=0.8,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=["invented"],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job_version"}) is None


def test_valid_score_is_clamped_to_unit_interval():
    score = SemanticProjectScore(
        score=2,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=["j1"],
    )
    validated = validate_semantic_score(score, {"r1": "resume", "j1": "job_version"})
    assert validated is not None
    assert validated.score == 1


def test_swapped_source_citations_are_rejected():
    score = SemanticProjectScore(
        score=0.9,
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

    assert SemanticMatcher(NoCallModel(), BrokenIndex(), Embedder()).score(_scope(), "resume", "job") is None


def test_trace_storage_failure_cannot_break_semantic_result():
    from app.ai.contracts import ModelResponse

    class Index:
        def search(self, scope, *args, **kwargs):
            source_type, source_id, _ = next(iter(scope.authorized_sources))
            return [_hit(source_type, source_id, source_type)]

    class Model:
        def generate(self, request):
            return ModelResponse(
                value=SemanticProjectScore(
                    score=0.8,
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

    result = SemanticMatcher(Model(), Index(), Embedder(), trace_sink=BrokenTrace()).score(_scope(), "resume", "job")
    assert result is not None
    assert result.score == 0.8
