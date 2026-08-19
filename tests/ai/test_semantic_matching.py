from __future__ import annotations

from app.ai.semantic_matching import SemanticProjectScore, validate_semantic_score


def test_score_requires_both_source_types():
    score = SemanticProjectScore(
        score=0.95,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=[],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job"}) is None


def test_unknown_citation_rejects_semantic_score():
    score = SemanticProjectScore(
        score=0.8,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=["invented"],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job"}) is None


def test_valid_score_is_clamped_to_unit_interval():
    score = SemanticProjectScore(
        score=2,
        rationale="fit",
        resume_citation_ids=["r1"],
        job_citation_ids=["j1"],
    )
    validated = validate_semantic_score(score, {"r1": "resume", "j1": "job"})
    assert validated is not None
    assert validated.score == 1


def test_swapped_source_citations_are_rejected():
    score = SemanticProjectScore(
        score=0.9,
        rationale="fit",
        resume_citation_ids=["j1"],
        job_citation_ids=["r1"],
    )
    assert validate_semantic_score(score, {"r1": "resume", "j1": "job"}) is None
