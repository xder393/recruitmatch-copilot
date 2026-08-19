from __future__ import annotations

from app.ai.contracts import ModelResponse
from app.ai.gateway import ModelGatewayError
from app.ai.resume_parser import LLMResumeParser
from app.resumes.parser import HeuristicResumeParser
from app.resumes.schemas import ResumeProfile
from tests.ai.fakes import FakeStructuredModel, RaisingModel


def _skill(name: str, start: int, end: int, text: str | None = None):
    return {"name": name, "evidence": {"start": start, "end": end, "text": text or name}}


class SequenceModel:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResponse(
            value=request.schema.model_validate(outcome),
            provider="fake",
            model="fake-model",
            input_tokens=1,
            output_tokens=1,
            estimated_cost=0,
            latency_ms=1,
        )


def test_parser_drops_llm_fact_whose_span_does_not_resolve_and_keeps_grounded_fallback():
    fake = FakeStructuredModel({"resume_extract": {"skills": [_skill("Kubernetes", 0, 10)]}})
    parser = LLMResumeParser(fake, HeuristicResumeParser())
    result = parser.parse("Python developer")
    assert [item.name for item in result.skills] == ["Python"]
    assert parser.last_outcome.invalid_fact_count == 1


def test_parser_accepts_projects_and_fields_with_exact_evidence():
    text = "本科，3年经验，负责RAG招聘助手"
    fake = FakeStructuredModel(
        {
            "resume_extract": {
                "skills": [_skill("RAG", 10, 13)],
                "experience_years": 3,
                "experience_evidence": {"start": 3, "end": 5, "text": "3年"},
                "education_level": "本科",
                "education_evidence": {"start": 0, "end": 2, "text": "本科"},
                "projects": [
                    {
                        "name": "招聘助手",
                        "description": "RAG招聘助手",
                        "evidence": {"start": 10, "end": 17, "text": "RAG招聘助手"},
                    }
                ],
            }
        }
    )
    result = LLMResumeParser(fake, HeuristicResumeParser()).parse(text)
    assert result.experience_years == 3
    assert result.education_level == "本科"
    assert result.projects[0].name == "招聘助手"


def test_parser_excludes_sensitive_trait_facts():
    text = "性别男，Python"
    fake = FakeStructuredModel(
        {
            "resume_extract": {
                "skills": [_skill("性别男", 0, 3), _skill("Python", 4, 10)],
                "projects": [],
            }
        }
    )
    result = LLMResumeParser(fake, HeuristicResumeParser()).parse(text)
    assert [item.name for item in result.skills] == ["Python"]


def test_invalid_output_gets_one_repair_request():
    model = SequenceModel(
        [
            ModelGatewayError("invalid_output", retryable=False),
            {"skills": [_skill("Python", 0, 6)]},
        ]
    )
    parser = LLMResumeParser(model, HeuristicResumeParser())
    assert parser.parse("Python").skills[0].name == "Python"
    assert len(model.calls) == 2
    assert "修复" in model.calls[1].system


def test_gateway_failure_uses_heuristic_parser():
    parser = LLMResumeParser(RaisingModel("timeout"), HeuristicResumeParser())
    result = parser.parse("3年 Python 本科")
    assert result.experience_years == 3
    assert result.experience_evidence.text == "3年"
    assert parser.last_outcome.fallback_reason == "timeout"
    assert parser.last_outcome.mode == "rules_fallback"


def test_profile_remains_backward_compatible():
    profile = ResumeProfile.model_validate({"skills": [], "experience_years": 2})
    assert profile.projects == []
    assert profile.experience_evidence is None
