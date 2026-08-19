"""Execute deterministic fake-model variants of the real RecruitMatch AI pipeline."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.ai.contracts import ModelRequest, ModelResponse  # noqa: E402
from app.ai.explanations import GroundedClaim, GroundedExplanationService, GroundedModelOutput  # noqa: E402
from app.ai.gateway import ModelGatewayError  # noqa: E402
from app.ai.resume_parser import LLMResumeParser  # noqa: E402
from app.ai.semantic_matching import SemanticMatcher, SemanticProjectScore  # noqa: E402
from app.evaluation.ai_metrics import evaluate_ai_cases  # noqa: E402
from app.knowledge.index import RetrievedChunk  # noqa: E402
from app.matching.engine import MatchingEngine  # noqa: E402
from app.matching.hybrid import HybridMatchingEngine, HybridTenantContext  # noqa: E402
from app.matching.schemas import CandidateJob  # noqa: E402
from app.resumes.parser import HeuristicResumeParser  # noqa: E402
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence  # noqa: E402


class DeterministicFakeModel:
    """Schema-aware local double; outputs are derived from prompts, never labels."""

    def __init__(self):
        self.calls: Counter[str] = Counter()
        self.input_tokens = 0
        self.output_tokens = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls[request.operation] += 1
        input_tokens = max(1, len(request.user) // 4)
        value = self._value(request)
        output_tokens = max(1, len(value.model_dump_json()) // 4)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        return ModelResponse(
            value=value,
            provider="fake",
            model="fake-structured-v1",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=0,
            latency_ms=1,
        )

    def _value(self, request: ModelRequest) -> BaseModel:
        if request.operation == "resume_extract":
            case_id = re.search(r"案例编号：([^；]+)", request.user)
            if case_id and case_id.group(1).endswith("-13"):
                raise ModelGatewayError("invalid_output", retryable=False)
            return self._resume(request.user)
        citation_ids = re.findall(r"\[citation:([^\]]+)\]", request.user)
        resume_ids = [item for item in citation_ids if item.startswith("resume:")]
        job_ids = [item for item in citation_ids if item.startswith("job:")]
        if request.operation == "semantic_project_match":
            if resume_ids and resume_ids[0].endswith("-14"):
                return SemanticProjectScore(
                    score=0.99,
                    rationale="故意交换引用以验证拒绝路径",
                    resume_citation_ids=job_ids[:1],
                    job_citation_ids=resume_ids[:1],
                )
            return SemanticProjectScore(
                score=0.75 if resume_ids and job_ids else 0,
                rationale="两侧项目证据已提供" if resume_ids and job_ids else "证据不足",
                resume_citation_ids=resume_ids[:1],
                job_citation_ids=job_ids[:1],
            )
        if request.operation == "match_explanation":
            both = resume_ids[:1] + job_ids[:1]
            strengths = [GroundedClaim(text="项目技能与职责存在交集", citation_ids=both)]
            if resume_ids and resume_ids[0].endswith("-15"):
                strengths.append(GroundedClaim(text="不受支持的结论", citation_ids=["invented"]))
            return GroundedModelOutput(
                summary=GroundedClaim(text="存在可核验的岗位适配证据", citation_ids=both),
                strengths=strengths,
                interview_questions=[GroundedClaim(text="请说明项目中的个人职责", citation_ids=both)],
            )
        raise ValueError(f"unsupported fake operation: {request.operation}")

    @staticmethod
    def _resume(text: str) -> ResumeProfile:
        first_line = text.split("；", 1)[0]
        raw_skills = first_line.split("：", 1)[-1]
        names = [item.strip() for item in re.split(r"[、,，]", raw_skills) if item.strip()]
        skills = []
        for name in names:
            start = text.find(name)
            if start >= 0:
                skills.append(
                    SkillEvidence(
                        name=name,
                        evidence=Evidence(start=start, end=start + len(name), text=name),
                    )
                )
        years = re.search(r"(\d+(?:\.\d+)?)\s*年", text)
        return ResumeProfile(skills=skills, experience_years=float(years.group(1)) if years else None)


class EvaluationIndex:
    def __init__(self, case_id: str, resume_text: str, jobs: list[CandidateJob]):
        self.case_id = case_id
        self.resume_text = resume_text
        self.jobs = {item.job_version_id: item for item in jobs}

    def source_chunks(self, tenant_id: str, source_type: str, source_id: str):
        del tenant_id
        if source_type == "resume" and source_id == self.case_id:
            return [self._hit(f"resume:{source_id}", "resume", source_id, self.resume_text)]
        if source_type == "job" and source_id in self.jobs:
            return [self._hit(f"job:{source_id}", "job", source_id, self.jobs[source_id].jd_text)]
        return []

    def search(self, tenant_id, query, source_types, top_k, min_score):
        del tenant_id, query, min_score
        if "policy" not in source_types or top_k <= 0:
            return []
        return [self._hit("policy:structured-interview", "policy", "policy-1", "面试结论必须引用候选人证据")]

    def resolve_citations(self, tenant_id: str, citation_ids: set[str]):
        del tenant_id
        candidates = [self._hit("resume:" + self.case_id, "resume", self.case_id, self.resume_text)]
        candidates.extend(
            self._hit(f"job:{source_id}", "job", source_id, job.jd_text)
            for source_id, job in self.jobs.items()
        )
        candidates.append(self._hit("policy:structured-interview", "policy", "policy-1", "面试结论必须引用候选人证据"))
        return [item for item in candidates if item.citation_id in citation_ids]

    @staticmethod
    def _hit(citation_id, source_type, source_id, content):
        return RetrievedChunk(citation_id, source_type, source_id, content, 0, len(content), None, 1)


def _jobs() -> list[CandidateJob]:
    templates = json.loads((_ROOT / "app" / "seeds" / "job_templates.json").read_text(encoding="utf-8"))
    return [
        CandidateJob(
            job_id=item["job_family"],
            job_version_id=f"{item['job_family']}-mid-v1",
            title=item["title"],
            jd_text=item["responsibilities"] + "；技能：" + "、".join(item["required_skills"]),
            profile={
                "required_skills": item["required_skills"],
                "preferred_skills": item["preferred_skills"],
                "min_experience_years": 3,
                "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
            },
        )
        for item in templates
    ]


def _claims(explanation) -> list[dict]:
    claims = [
        explanation.summary,
        *explanation.strengths,
        *explanation.gaps,
        *explanation.risk_flags,
        *explanation.interview_questions,
    ]
    return [item.model_dump(mode="json") for item in claims if item is not None]


def evaluate(dataset: list[dict], mode: str) -> dict:
    model = DeterministicFakeModel()
    rules = MatchingEngine()
    jobs = _jobs()
    evaluated = []
    parser_outcomes: Counter[str] = Counter()
    grounding_outcomes: Counter[str] = Counter()
    semantic_fallbacks = 0
    for case in dataset:
        before_input, before_output = model.input_tokens, model.output_tokens
        before_calls = sum(model.calls.values())
        if mode == "rules-v1":
            profile = HeuristicResumeParser().parse(case["resume_text"])
            parser_outcomes["rules"] += 1
        else:
            parsed = LLMResumeParser(model, HeuristicResumeParser()).parse_with_metadata(case["resume_text"])
            profile = parsed.profile
            parser_outcomes[parsed.outcome.mode] += 1
        index = EvaluationIndex(case["id"], case["resume_text"], jobs)
        if mode == "hybrid-v1":
            ranked = HybridMatchingEngine(rules, SemanticMatcher(model, index)).rank(
                profile,
                jobs,
                HybridTenantContext("synthetic-tenant", case["id"]),
                top_k=3,
            )
        else:
            ranked = rules.rank(profile, jobs, top_k=3)
        semantic_fallbacks += sum(
            1 for item in ranked if getattr(item, "semantic_score", 1) is None
        )

        explanation_claims = []
        authorized_ids = []
        if mode != "rules-v1" and ranked:
            top = ranked[0]
            hits = index.source_chunks("synthetic-tenant", "resume", case["id"])
            hits += index.source_chunks("synthetic-tenant", "job", top.job_version_id)
            hits += index.search("synthetic-tenant", top.title, {"policy"}, 3, 0)
            explanation = GroundedExplanationService(
                model,
                citation_resolver=index.resolve_citations,
            ).generate(
                "synthetic-tenant",
                case["id"],
                top.job_version_id,
                {
                    "matched": top.matched_items,
                    "missing": top.missing_items,
                    "uncertain": top.uncertain_items,
                },
                hits,
            )
            explanation_claims = _claims(explanation)
            grounding_outcomes[explanation.grounding_status] += 1
            authorized_ids = [item.citation_id for item in hits]
        evaluated.append(
            {
                "expected_facts": case["expected_facts"],
                "extracted_facts": [item.name for item in profile.skills],
                "expected_job_families": case["expected_job_families"],
                "predictions": [item.job_id for item in ranked],
                "claims": explanation_claims,
                "authorized_ids": authorized_ids,
                "latency_ms": sum(model.calls.values()) - before_calls,
                "input_tokens": model.input_tokens - before_input,
                "output_tokens": model.output_tokens - before_output,
                "estimated_cost": 0,
            }
        )
    return {
        "dataset_version": "recruitmatch-ai-v1",
        "algorithm_version": mode,
        "model_version": "fake-structured-v1" if mode != "rules-v1" else "none",
        "prompt_version": "semantic-project-v1" if mode == "hybrid-v1" else (
            "resume-extract-v1" if mode == "llm-rules-v1" else "none"
        ),
        "embedding_version": "fake-index-v1" if mode == "hybrid-v1" else "none",
        "label_source": "synthetic_ai",
        "pipeline_calls": dict(sorted(model.calls.items())),
        "pipeline_outcomes": {
            "parser": dict(sorted(parser_outcomes.items())),
            "grounding": dict(sorted(grounding_outcomes.items())),
            "semantic_fallback_results": semantic_fallbacks,
        },
        "metrics": evaluate_ai_cases(evaluated).model_dump(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evaluation/recruitmatch-ai-v1.json")
    parser.add_argument("--output", default="evaluation/recruitmatch-ai-v1-results.json")
    parser.add_argument("--mode", choices=["rules-v1", "llm-rules-v1", "hybrid-v1"], default="hybrid-v1")
    parser.add_argument("--fake-model", action="store_true")
    args = parser.parse_args()
    if not args.fake_model:
        parser.error("this reproducible benchmark currently requires --fake-model")
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    result = evaluate(dataset, args.mode)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
