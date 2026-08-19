"""Deterministic rules-v1 matching algorithm."""
from __future__ import annotations

from typing import Dict, List

from app.matching.schemas import CandidateJob, DimensionScores, MatchRecommendation
from app.resumes.schemas import ResumeProfile, SkillEvidence


def _key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _coverage(requirements: List[str], available: Dict[str, SkillEvidence]) -> float:
    if not requirements:
        return 1.0
    matched = sum(1 for item in requirements if _key(item) in available)
    return matched / len(requirements)


class MatchingEngine:
    algorithm_version = "rules-v1"

    def rank(
        self,
        profile: ResumeProfile,
        jobs: List[CandidateJob],
        top_k: int = 3,
    ) -> List[MatchRecommendation]:
        available = {_key(item.name): item for item in profile.skills}
        recommendations = [self._score(profile, available, job) for job in jobs]
        recommendations.sort(key=lambda item: item.total_score, reverse=True)
        return recommendations[:top_k]

    def _score(
        self,
        profile: ResumeProfile,
        available: Dict[str, SkillEvidence],
        job: CandidateJob,
    ) -> MatchRecommendation:
        required = list(job.profile.get("required_skills") or [])
        preferred = list(job.profile.get("preferred_skills") or [])
        required_coverage = _coverage(required, available)
        preferred_coverage = _coverage(preferred, available) if preferred else 1.0
        skills_score = 0.8 * required_coverage + 0.2 * preferred_coverage

        minimum = float(job.profile.get("min_experience_years") or 0)
        uncertain = []
        risk_flags = []
        if profile.experience_years is None and minimum > 0:
            experience_score = 0.5
            uncertain.append(f"工作年限未知（岗位要求 {int(minimum) if minimum.is_integer() else minimum} 年）")
            risk_flags.append("experience_unknown")
        elif minimum <= 0:
            experience_score = 1.0
        else:
            experience_score = min(float(profile.experience_years or 0) / minimum, 1.0)

        matched_requirements = [item for item in required if _key(item) in available]
        matched_preferences = [item for item in preferred if _key(item) in available]
        matched_items = matched_requirements + matched_preferences
        missing_items = [item for item in required if _key(item) not in available]
        if missing_items:
            risk_flags.append("hard_requirement_gap")

        project_score = preferred_coverage
        weights = job.profile.get("weights") or {"skills": 0.5, "experience": 0.3, "projects": 0.2}
        skill_weight = float(weights.get("skills", 0.5))
        experience_weight = float(weights.get("experience", 0.3))
        project_weight = float(weights.get("projects", 0.2))
        weight_total = skill_weight + experience_weight + project_weight or 1.0
        total = (
            skills_score * skill_weight + experience_score * experience_weight + project_score * project_weight
        ) / weight_total

        evidence = [available[_key(item)].evidence for item in matched_items]
        summary = f"匹配 {len(matched_items)} 项，缺失 {len(missing_items)} 项，存在 {len(uncertain)} 项待确认"
        return MatchRecommendation(
            job_id=job.job_id,
            job_version_id=job.job_version_id,
            title=job.title,
            total_score=round(total, 4),
            dimension_scores=DimensionScores(
                skills=round(skills_score, 4),
                experience=round(experience_score, 4),
                projects=round(project_score, 4),
            ),
            matched_items=matched_items,
            missing_items=missing_items,
            uncertain_items=uncertain,
            evidence=evidence,
            risk_flags=risk_flags,
            summary=summary,
        )
