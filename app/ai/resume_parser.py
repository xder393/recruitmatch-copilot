"""Evidence-bound LLM resume parser with deterministic fallback."""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.contracts import ModelRequest, StructuredModel
from app.ai.evidence import contains_sensitive_trait, evidence_resolves
from app.ai.gateway import ModelGatewayError
from app.resumes.schemas import ResumeProfile, SkillEvidence


@dataclass(frozen=True)
class ParseOutcome:
    mode: str
    invalid_fact_count: int = 0
    fallback_reason: str | None = None


class LLMResumeParser:
    def __init__(
        self,
        model: StructuredModel,
        fallback,
        prompt_version: str = "resume-extract-v1",
        max_evidence_characters: int = 8000,
    ):
        self.model = model
        self.fallback = fallback
        self.prompt_version = prompt_version
        self.max_evidence_characters = max_evidence_characters
        self.last_outcome = ParseOutcome(mode="not_run")

    def parse(self, text: str) -> ResumeProfile:
        bounded = text[: self.max_evidence_characters]
        request = self._request(bounded, repair=False)
        repaired = False
        try:
            response = self.model.generate(request)
        except ModelGatewayError as exc:
            if exc.code != "invalid_output":
                return self._fallback(text, exc.code)
            repaired = True
            try:
                response = self.model.generate(self._request(bounded, repair=True))
            except ModelGatewayError as repair_error:
                return self._fallback(text, repair_error.code)
        validated, invalid_count = self._ground(response.value, bounded)
        merged = self._merge(validated, self.fallback.parse(text))
        self.last_outcome = ParseOutcome(
            mode="llm_repaired" if repaired else "llm",
            invalid_fact_count=invalid_count,
        )
        return merged

    def _request(self, text: str, repair: bool) -> ModelRequest[ResumeProfile]:
        system = (
            "仅提取简历明确出现的事实；未知字段返回 null；每个事实复制精确原文及字符偏移；"
            "禁止提取或推断性别、年龄、照片、民族、婚育等敏感属性。"
        )
        if repair:
            system += " 上一次输出不符合 Schema，请修复格式且不得增加新事实。"
        return ModelRequest(
            operation="resume_extract",
            prompt_version=self.prompt_version,
            system=system,
            user=text,
            schema=ResumeProfile,
        )

    def _fallback(self, text: str, reason: str) -> ResumeProfile:
        self.last_outcome = ParseOutcome(mode="rules_fallback", fallback_reason=reason)
        return self.fallback.parse(text)

    @staticmethod
    def _ground(profile: ResumeProfile, source: str) -> tuple[ResumeProfile, int]:
        invalid = 0
        skills = []
        for item in profile.skills:
            if evidence_resolves(source, item.evidence) and not contains_sensitive_trait(item.name):
                skills.append(item)
            else:
                invalid += 1

        projects = []
        for item in profile.projects:
            if (
                evidence_resolves(source, item.evidence)
                and not contains_sensitive_trait(item.name)
                and not contains_sensitive_trait(item.description)
            ):
                projects.append(item)
            else:
                invalid += 1

        experience_years = profile.experience_years
        experience_evidence = profile.experience_evidence
        if experience_years is not None and not evidence_resolves(source, experience_evidence):
            invalid += 1
            experience_years = None
            experience_evidence = None

        education_level = profile.education_level
        education_evidence = profile.education_evidence
        if education_level is not None and (
            not evidence_resolves(source, education_evidence) or contains_sensitive_trait(education_level)
        ):
            invalid += 1
            education_level = None
            education_evidence = None

        return (
            ResumeProfile(
                schema_version=profile.schema_version,
                skills=skills,
                experience_years=experience_years,
                experience_evidence=experience_evidence,
                education_level=education_level,
                education_evidence=education_evidence,
                projects=projects,
            ),
            invalid,
        )

    @staticmethod
    def _merge(llm: ResumeProfile, rules: ResumeProfile) -> ResumeProfile:
        seen = {item.name.casefold() for item in llm.skills}
        skills: list[SkillEvidence] = list(llm.skills)
        skills.extend(item for item in rules.skills if item.name.casefold() not in seen)
        return ResumeProfile(
            schema_version=llm.schema_version,
            skills=skills,
            experience_years=llm.experience_years if llm.experience_years is not None else rules.experience_years,
            experience_evidence=llm.experience_evidence or rules.experience_evidence,
            education_level=llm.education_level or rules.education_level,
            education_evidence=llm.education_evidence or rules.education_evidence,
            projects=list(llm.projects),
        )
