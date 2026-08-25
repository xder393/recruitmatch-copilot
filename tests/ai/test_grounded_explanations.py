from __future__ import annotations

from app.ai.contracts import ModelResponse
from app.ai.explanations import (
    GroundedClaim,
    GroundedExplanationService,
    GroundedModelOutput,
)
from app.retrieval import RetrievedChunk, SearchScope


class FakeModel:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return ModelResponse(
            value=self.output,
            provider="fake",
            model="fake",
            input_tokens=10,
            output_tokens=5,
            estimated_cost=0,
            latency_ms=1,
        )


def _hit(citation_id="known", source_type="resume", source_id="resume-1", content="Python"):
    return RetrievedChunk(
        id=citation_id,
        tenant_id="tenant-1",
        citation_id=citation_id,
        source_type=source_type,
        source_id=source_id,
        source_version="v1",
        generation=1,
        content=content,
        start_offset=0,
        end_offset=len(content),
        page_number=None,
        section=None,
        score=0.9,
    )


def _scope(*hits):
    authorized = frozenset((hit.source_type, hit.source_id, hit.source_version) for hit in hits)
    return SearchScope("tenant-1", frozenset(hit.source_type for hit in hits), authorized)


def _rules():
    return {"matched": ["Python"], "missing": ["RAG"], "uncertain": ["leadership"]}


def test_unknown_and_empty_citations_remove_unsupported_claims():
    model = FakeModel(
        GroundedModelOutput(
            summary=GroundedClaim(text="有相关基础", citation_ids=["known"]),
            strengths=[
                GroundedClaim(text="Python 专家", citation_ids=["invented"]),
                GroundedClaim(text="沟通良好", citation_ids=[]),
            ],
            gaps=[GroundedClaim(text="缺少 RAG", citation_ids=["known"])],
            risk_flags=[],
            interview_questions=[],
        )
    )
    hit = _hit()
    result = GroundedExplanationService(model).generate(_scope(hit), "resume-1", "job-1", _rules(), [hit])

    assert result.strengths == []
    assert [item.text for item in result.gaps] == ["缺少 RAG"]
    assert result.grounding_status == "rejected_unsupported_claims"
    assert set(result.citations) == {"known"}


def test_no_hits_skips_model_and_returns_rules_fallback():
    model = FakeModel()
    result = GroundedExplanationService(model).generate(
        SearchScope("tenant-1", frozenset({"resume"}), frozenset()), "resume-1", "job-1", _rules(), []
    )

    assert result.grounding_status == "insufficient_evidence"
    assert model.calls == []
    assert [item.text for item in result.strengths] == ["已匹配：Python"]
    assert [item.text for item in result.gaps] == ["待补足：RAG"]


def test_context_only_contains_authorized_sources_and_is_bounded():
    model = FakeModel(GroundedModelOutput(summary=None))
    hits = [
        _hit("resume", "resume", "resume-1", "A" * 30),
        _hit("job", "job_version", "job-1", "B" * 30),
        _hit("policy", "knowledge_document", "policy-1", "C" * 30),
        _hit("other-resume", "resume", "resume-2", "SECRET"),
        _hit("other-job", "job_version", "job-2", "SECRET"),
    ]
    GroundedExplanationService(model, max_evidence_characters=90).generate(
        _scope(*hits), "resume-1", "job-1", _rules(), hits
    )

    prompt = model.calls[0].user
    assert "other-resume" not in prompt and "other-job" not in prompt
    assert "SECRET" not in prompt
    assert len(prompt) <= 90


def test_disabled_generation_uses_deterministic_fallback():
    model = FakeModel()
    result = GroundedExplanationService(model, enabled=False).generate(
        _scope(_hit()), "resume-1", "job-1", _rules(), [_hit()]
    )

    assert result.grounding_status == "rules_fallback"
    assert model.calls == []


def test_empty_valid_model_output_is_not_reported_as_grounded():
    result = GroundedExplanationService(FakeModel(GroundedModelOutput())).generate(
        _scope(_hit()), "resume-1", "job-1", _rules(), [_hit()]
    )

    assert result.grounding_status == "empty_model_output"
    assert [item.text for item in result.strengths] == ["已匹配：Python"]


def test_trace_storage_failure_cannot_break_grounded_result():
    class BrokenTrace:
        def succeeded(self, *args, **kwargs):
            raise RuntimeError("trace database unavailable")

    result = GroundedExplanationService(
        FakeModel(GroundedModelOutput(summary=GroundedClaim(text="Python", citation_ids=["known"]))),
        trace_sink=BrokenTrace(),
    ).generate(_scope(_hit()), "resume-1", "job-1", _rules(), [_hit()])

    assert result.grounding_status == "grounded"
