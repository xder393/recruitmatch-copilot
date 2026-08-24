"""Citation-grounded recruiting guidance with deterministic degradation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import time

from pydantic import BaseModel, Field

from app.ai.citations import authorized_hits, citations_are_known, format_evidence
from app.ai.contracts import ModelRequest, StructuredModel
from app.ai.gateway import ModelGatewayError
from app.knowledge.index import RetrievedChunk


class GroundedClaim(BaseModel):
    text: str
    citation_ids: List[str] = Field(default_factory=list)


class GroundedModelOutput(BaseModel):
    summary: Optional[GroundedClaim] = None
    strengths: List[GroundedClaim] = Field(default_factory=list)
    gaps: List[GroundedClaim] = Field(default_factory=list)
    risk_flags: List[GroundedClaim] = Field(default_factory=list)
    interview_questions: List[GroundedClaim] = Field(default_factory=list)


class Citation(BaseModel):
    source_type: str
    source_id: str
    content: str
    start: int
    end: int
    page: Optional[int] = None


class GroundedExplanation(BaseModel):
    summary: Optional[GroundedClaim] = None
    strengths: List[GroundedClaim] = Field(default_factory=list)
    gaps: List[GroundedClaim] = Field(default_factory=list)
    risk_flags: List[GroundedClaim] = Field(default_factory=list)
    interview_questions: List[GroundedClaim] = Field(default_factory=list)
    citations: Dict[str, Citation] = Field(default_factory=dict)
    grounding_status: str


class GroundedExplanationService:
    def __init__(
        self,
        model: StructuredModel,
        *,
        enabled: bool = True,
        prompt_version: str = "match-explanation-v1",
        max_evidence_characters: int = 8000,
        citation_resolver=None,
        trace_sink=None,
    ):
        self.model = model
        self.enabled = enabled
        self.prompt_version = prompt_version
        self.max_evidence_characters = max_evidence_characters
        self.citation_resolver = citation_resolver
        self.trace_sink = trace_sink

    def generate(
        self,
        tenant_id: str,
        resume_id: str,
        job_version_id: str,
        rule_result: Dict[str, Any],
        hits: List[RetrievedChunk],
    ) -> GroundedExplanation:
        if not self.enabled:
            return self._fallback(rule_result, "rules_fallback")

        permitted = authorized_hits(hits, resume_id, job_version_id)
        if self.citation_resolver is not None:
            permitted = self.citation_resolver(tenant_id, {hit.citation_id for hit in permitted})
            permitted = authorized_hits(permitted, resume_id, job_version_id)
        evidence = format_evidence(permitted, self.max_evidence_characters)
        if not evidence:
            return self._fallback(rule_result, "insufficient_evidence")

        request = ModelRequest(
            operation="match_explanation",
            prompt_version=self.prompt_version,
            system=(
                "仅依据给定证据生成招聘辅助说明。每一条结论和面试问题必须引用至少一个"
                "[citation:<id>]；不得推断敏感属性；证据不足时不要生成结论。"
            ),
            user=evidence,
            schema=GroundedModelOutput,
        )
        started = time.perf_counter()
        source_ids = [hit.source_id for hit in permitted]
        try:
            response = self.model.generate(request)
        except ModelGatewayError as error:
            self._trace_failed(tenant_id, resume_id, source_ids, request, error, started)
            return self._fallback(rule_result, "rules_fallback")
        except Exception:
            error = ModelGatewayError("unexpected_model_error", retryable=False)
            self._trace_failed(tenant_id, resume_id, source_ids, request, error, started)
            return self._fallback(rule_result, "rules_fallback")
        explanation = self._validate(response.value, permitted)
        if not any(
            [
                explanation.summary,
                explanation.strengths,
                explanation.gaps,
                explanation.risk_flags,
                explanation.interview_questions,
            ]
        ):
            explanation = self._fallback(rule_result, "empty_model_output")
        if self.trace_sink is not None:
            try:
                self.trace_sink.succeeded(
                    tenant_id,
                    "match_explanation",
                    resume_id,
                    source_ids,
                    request,
                    response,
                    fallback_reason=(
                        None if explanation.grounding_status == "grounded" else explanation.grounding_status
                    ),
                )
            except Exception:
                pass
        return explanation

    def _trace_failed(self, tenant_id, resume_id, source_ids, request, error, started):
        if self.trace_sink is not None:
            try:
                self.trace_sink.failed(
                    tenant_id,
                    "match_explanation",
                    resume_id,
                    source_ids,
                    request,
                    error,
                    (time.perf_counter() - started) * 1000,
                )
            except Exception:
                pass

    @staticmethod
    def _validate(output: GroundedModelOutput, hits: List[RetrievedChunk]) -> GroundedExplanation:
        known_ids = {hit.citation_id for hit in hits}
        rejected = False

        def claim(item: Optional[GroundedClaim]) -> Optional[GroundedClaim]:
            nonlocal rejected
            if item is None:
                return None
            if not citations_are_known(item.citation_ids, known_ids):
                rejected = True
                return None
            return item

        def claims(items: List[GroundedClaim]) -> List[GroundedClaim]:
            return [kept for item in items if (kept := claim(item)) is not None]

        summary = claim(output.summary)
        strengths = claims(output.strengths)
        gaps = claims(output.gaps)
        risk_flags = claims(output.risk_flags)
        questions = claims(output.interview_questions)
        used_ids = {
            citation_id
            for item in [summary, *strengths, *gaps, *risk_flags, *questions]
            if item is not None
            for citation_id in item.citation_ids
        }
        by_id = {hit.citation_id: hit for hit in hits}
        citations = {
            citation_id: Citation(
                source_type=by_id[citation_id].source_type,
                source_id=by_id[citation_id].source_id,
                content=by_id[citation_id].content,
                start=by_id[citation_id].start,
                end=by_id[citation_id].end,
                page=by_id[citation_id].page,
            )
            for citation_id in used_ids
        }
        return GroundedExplanation(
            summary=summary,
            strengths=strengths,
            gaps=gaps,
            risk_flags=risk_flags,
            interview_questions=questions,
            citations=citations,
            grounding_status="rejected_unsupported_claims" if rejected else "grounded",
        )

    @staticmethod
    def _fallback(rule_result: Dict[str, Any], status: str) -> GroundedExplanation:
        strengths = [GroundedClaim(text=f"已匹配：{item}") for item in rule_result.get("matched", [])]
        gaps = [GroundedClaim(text=f"待补足：{item}") for item in rule_result.get("missing", [])]
        risks = [GroundedClaim(text=f"待核实：{item}") for item in rule_result.get("uncertain", [])]
        questions = [
            GroundedClaim(text=f"请结合项目证据说明：{item}")
            for item in [*rule_result.get("missing", []), *rule_result.get("uncertain", [])]
        ]
        return GroundedExplanation(
            strengths=strengths,
            gaps=gaps,
            risk_flags=risks,
            interview_questions=questions,
            grounding_status=status,
        )
