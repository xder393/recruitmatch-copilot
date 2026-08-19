from __future__ import annotations


def _profile(experience_years=5):
    from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence

    return ResumeProfile(
        skills=[
            SkillEvidence(name="Python", evidence=Evidence(start=0, end=6, text="Python")),
            SkillEvidence(name="FastAPI", evidence=Evidence(start=7, end=14, text="FastAPI")),
            SkillEvidence(name="RAG", evidence=Evidence(start=15, end=18, text="RAG")),
        ],
        experience_years=experience_years,
    )


def _job(identifier, title, required, preferred, minimum=0):
    from app.matching.schemas import CandidateJob

    return CandidateJob(
        job_id=identifier,
        job_version_id=f"{identifier}-v1",
        title=title,
        jd_text=" ".join(required + preferred),
        profile={
            "required_skills": required,
            "preferred_skills": preferred,
            "min_experience_years": minimum,
            "weights": {"skills": 0.5, "experience": 0.3, "projects": 0.2},
        },
    )


def test_rank_orders_top_jobs_and_preserves_skill_evidence():
    """Catches ranking unrelated jobs or emitting unverifiable matched skills."""
    from app.matching.engine import MatchingEngine

    jobs = [
        _job("frontend", "前端", ["JavaScript", "React"], ["TypeScript"], 3),
        _job("ai", "AI 应用", ["Python", "FastAPI"], ["RAG"], 3),
        _job("backend", "后端", ["Python", "MySQL"], ["Redis"], 3),
        _job("sre", "SRE", ["Linux", "Docker"], ["Kubernetes"], 3),
    ]

    ranked = MatchingEngine().rank(_profile(), jobs, top_k=3)

    assert [item.job_id for item in ranked] == ["ai", "backend", "frontend"]
    assert ranked[0].total_score == 1.0
    assert ranked[0].matched_items == ["Python", "FastAPI", "RAG"]
    assert [item.text for item in ranked[0].evidence] == ["Python", "FastAPI", "RAG"]
    assert ranked[1].missing_items == ["MySQL"]
    assert "hard_requirement_gap" in ranked[1].risk_flags


def test_unknown_experience_is_uncertain_instead_of_invented():
    """Catches missing experience being treated as confirmed pass or failure."""
    from app.matching.engine import MatchingEngine

    result = MatchingEngine().rank(
        _profile(experience_years=None),
        [_job("ai", "AI 应用", ["Python"], ["RAG"], 5)],
    )[0]

    assert result.dimension_scores.experience == 0.5
    assert result.uncertain_items == ["工作年限未知（岗位要求 5 年）"]
    assert "experience_unknown" in result.risk_flags
    assert "hire" not in result.model_dump_json().lower()
    assert "reject" not in result.model_dump_json().lower()
