"""Fixed-weight hybrid ranking over stable rules-v1 recommendations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Optional

from app.matching.schemas import CandidateJob, HybridMatchRecommendation
from app.resumes.schemas import ResumeProfile
from app.retrieval.ports import SearchScope


def combine_scores(rule_score: float, semantic_score: Optional[float]) -> float:
    bounded_rule = min(max(float(rule_score), 0.0), 1.0)
    bounded_semantic = (
        float(semantic_score) / 100.0
        if semantic_score is not None and math.isfinite(float(semantic_score)) and 0 <= semantic_score <= 100
        else 0.0
    )
    return round(0.8 * bounded_rule + 0.2 * bounded_semantic, 4)


@dataclass(frozen=True)
class HybridTenantContext:
    resume_id: str
    search_scope: SearchScope


class HybridMatchingEngine:
    algorithm_version = "hybrid-v1"

    def __init__(self, rules_engine, semantic_matcher):
        self.rules_engine = rules_engine
        self.semantic_matcher = semantic_matcher

    def rank(
        self,
        profile: ResumeProfile,
        jobs: List[CandidateJob],
        tenant_context: HybridTenantContext,
        top_k: int = 3,
    ) -> List[HybridMatchRecommendation]:
        rule_results = self.rules_engine.rank(profile, jobs, top_k=len(jobs))
        recommendations = []
        for rule in rule_results:
            semantic = self.semantic_matcher.score(
                tenant_context.search_scope,
                tenant_context.resume_id,
                rule.job_version_id,
                self._resume_summary(profile),
                next(job.jd_text for job in jobs if job.job_version_id == rule.job_version_id),
            )
            invalid_semantic = semantic is not None and (
                not math.isfinite(float(semantic.score)) or not 0 <= semantic.score <= 100
            )
            if invalid_semantic:
                semantic = None
            citation_ids = []
            if semantic is not None:
                citation_ids = semantic.resume_citation_ids + semantic.job_citation_ids
            payload = rule.model_dump()
            payload["total_score"] = combine_scores(rule.total_score, semantic.score if semantic else None)
            recommendations.append(
                HybridMatchRecommendation(
                    **payload,
                    rule_score=rule.total_score,
                    semantic_score=semantic.score if semantic else None,
                    grounding_status="grounded" if semantic else "rules_fallback",
                    fallback_reason=(
                        None
                        if semantic
                        else "invalid_semantic_score"
                        if invalid_semantic
                        else "semantic_evidence_unavailable"
                    ),
                    citations=citation_ids,
                )
            )
        recommendations.sort(key=lambda item: (-item.total_score, item.job_id))
        return recommendations[:top_k]

    @staticmethod
    def _resume_summary(profile: ResumeProfile) -> str:
        skills = "、".join(item.name for item in profile.skills)
        projects = "\n".join(f"{item.name}: {item.description}" for item in profile.projects)
        return f"技能：{skills}\n项目：{projects}".strip()
