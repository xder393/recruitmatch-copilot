from __future__ import annotations


def test_profile_skills_preserve_exact_source_evidence():
    """Catches inferred skills that cannot be verified against resume text."""
    from app.resumes.parser import HeuristicResumeParser

    text = "项目：使用 Python 和 FastAPI 构建 RAG 系统"
    profile = HeuristicResumeParser().parse(text)

    assert [skill.name for skill in profile.skills] == ["Python", "FastAPI", "RAG"]
    for skill in profile.skills:
        assert text[skill.evidence.start : skill.evidence.end] == skill.evidence.text


def test_profile_marks_missing_experience_unknown_and_excludes_sensitive_fields():
    """Catches guessing experience or carrying protected attributes into matching."""
    from app.resumes.parser import HeuristicResumeParser

    dumped = HeuristicResumeParser().parse("熟悉 Java 和 MySQL").model_dump()
    assert dumped["experience_years"] is None
    assert not {"gender", "age", "marital_status", "photo"}.intersection(dumped)
