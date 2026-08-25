from __future__ import annotations

from app.ai.semantic_matching import SemanticProjectScore
from app.matching.engine import MatchingEngine
from app.matching.hybrid import HybridMatchingEngine, HybridTenantContext, combine_scores
from app.matching.schemas import CandidateJob
from app.resumes.schemas import Evidence, ResumeProfile, SkillEvidence
from app.retrieval import SearchScope


class FakeSemanticMatcher:
    def __init__(self, scores):
        self.scores = scores

    def score(self, scope, resume_id, job_version_id):
        return self.scores.get(job_version_id)


def _context():
    return HybridTenantContext(
        "resume-1",
        SearchScope("t1", frozenset({"resume"}), frozenset({("resume", "resume-1", "v1")})),
    )


def _profile():
    return ResumeProfile(skills=[SkillEvidence(name="Python", evidence=Evidence(start=0, end=6, text="Python"))])


def _job(identifier, required):
    return CandidateJob(
        job_id=identifier,
        job_version_id=f"{identifier}-v1",
        title=identifier,
        jd_text=" ".join(required),
        profile={"required_skills": required, "preferred_skills": [], "weights": {"skills": 1}},
    )


def test_hybrid_formula_is_fixed_and_model_score_is_clamped():
    assert combine_scores(0.70, 0.90) == 0.74
    assert combine_scores(0.5, 2.0) == 0.6
    assert combine_scores(0.5, None) == 0.4


def test_hybrid_reranks_and_retains_rule_components():
    jobs = [_job("python", ["Python"]), _job("java", ["Java"])]
    matcher = FakeSemanticMatcher(
        {
            "python-v1": SemanticProjectScore(
                score=0,
                rationale="none",
                resume_citation_ids=["r1"],
                job_citation_ids=["j1"],
            ),
            "java-v1": SemanticProjectScore(
                score=1,
                rationale="project fit",
                resume_citation_ids=["r2"],
                job_citation_ids=["j2"],
            ),
        }
    )
    ranked = HybridMatchingEngine(MatchingEngine(), matcher).rank(_profile(), jobs, _context(), top_k=2)

    assert ranked[0].job_id == "python"
    assert ranked[0].rule_score == 1
    assert ranked[0].total_score == 0.8
    assert ranked[1].semantic_score == 1
    assert ranked[1].citations == ["r2", "j2"]


def test_missing_semantic_output_has_explicit_fallback():
    result = HybridMatchingEngine(MatchingEngine(), FakeSemanticMatcher({})).rank(
        _profile(), [_job("python", ["Python"])], _context()
    )[0]
    assert result.total_score == 0.8
    assert result.semantic_score is None
    assert result.fallback_reason == "semantic_evidence_unavailable"
