"""Evidence-bound LLM resume parser with deterministic fallback."""

from __future__ import annotations

from dataclasses import dataclass
import time

from app.ai.contracts import ModelRequest, ModelResponse, StructuredModel
from app.ai.evidence import contains_sensitive_trait, evidence_resolves
from app.ai.gateway import ModelGatewayError
from app.resumes.schemas import ResumeProfile, SkillEvidence


@dataclass(frozen=True)
class ParseOutcome:
    mode: str
    invalid_fact_count: int = 0
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ResumeParseResult:
    profile: ResumeProfile
    outcome: ParseOutcome
    request: ModelRequest[ResumeProfile]
    response: ModelResponse[ResumeProfile] | None = None
    error: ModelGatewayError | None = None
    latency_ms: float = 0.0


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
        return self.parse_with_metadata(text).profile

    def parse_with_metadata(self, text: str) -> ResumeParseResult:
        started = time.perf_counter()
        bounded = text[: self.max_evidence_characters]
        request = self._request(bounded, repair=False)
        repaired = False
        try:
            response = self.model.generate(request)
        except ModelGatewayError as exc:
            if exc.code != "invalid_output":
                return self._fallback_result(text, request, exc, started)
            repaired = True
            request = self._request(bounded, repair=True)
            try:
                response = self.model.generate(request)
            except ModelGatewayError as repair_error:
                return self._fallback_result(text, request, repair_error, started)
        validated, invalid_count = self._ground(response.value, bounded)
        merged = self._merge(validated, self.fallback.parse(text))
        self.last_outcome = ParseOutcome(
            mode="llm_repaired" if repaired else "llm",
            invalid_fact_count=invalid_count,
        )
        return ResumeParseResult(
            profile=merged,
            outcome=self.last_outcome,
            request=request,
            response=response,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

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

    def _fallback_result(
        self,
        text: str,
        request: ModelRequest[ResumeProfile],
        error: ModelGatewayError,
        started: float,
    ) -> ResumeParseResult:
        self.last_outcome = ParseOutcome(mode="rules_fallback", fallback_reason=error.code)
        return ResumeParseResult(
            profile=self.fallback.parse(text),
            outcome=self.last_outcome,
            request=request,
            error=error,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _ground(profile: ResumeProfile, source: str) -> tuple[ResumeProfile, int]:
        invalid = 0
        skills = []
        for skill in profile.skills:
            if evidence_resolves(source, skill.evidence) and not contains_sensitive_trait(skill.name):
                skills.append(skill)
            else:
                invalid += 1

        projects = []
        for project in profile.projects:
            if (
                evidence_resolves(source, project.evidence)
                and not contains_sensitive_trait(project.name)
                and not contains_sensitive_trait(project.description)
            ):
                projects.append(project)
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
