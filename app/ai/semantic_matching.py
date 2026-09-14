"""Bounded semantic project-fit scoring that requires two-sided citations."""

from __future__ import annotations

import math
from typing import Dict, List, Optional
import time

from pydantic import BaseModel, Field

from app.ai.citations import format_evidence_with_citation_ids
from app.ai.contracts import ModelRequest, StructuredModel
from app.ai.gateway import ModelGatewayError
from app.retrieval.indexing import EmbeddingAdapter
from app.retrieval.ports import RecruitingVectorIndex, SearchScope
from app.observability.events import observed, record


class SemanticProjectScore(BaseModel):
    score: float
    rationale: str
    resume_citation_ids: List[str] = Field(default_factory=list)
    job_citation_ids: List[str] = Field(default_factory=list)


@observed("citation.validate", {"operation": "semantic_project_match"})
def validate_semantic_score(
    score: SemanticProjectScore,
    authorized_sources: Dict[str, str],
) -> Optional[SemanticProjectScore]:
    numeric_score = float(score.score)
    if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 100.0:
        return None
    if not score.resume_citation_ids or not score.job_citation_ids:
        return None
    if any(authorized_sources.get(item) != "resume" for item in score.resume_citation_ids):
        return None
    if any(authorized_sources.get(item) != "job_version" for item in score.job_citation_ids):
        return None
    return score.model_copy(update={"score": numeric_score})


class SemanticMatcher:
    def __init__(
        self,
        model: StructuredModel,
        source_index: RecruitingVectorIndex,
        embedder: EmbeddingAdapter,
        *,
        enabled: bool = True,
        prompt_version: str = "semantic-project-v1",
        max_evidence_characters: int = 8000,
        top_k: int = 6,
        min_score: float = 0.35,
        trace_sink=None,
    ):
        self.model = model
        self.source_index = source_index
        self.embedder = embedder
        self.enabled = enabled
        self.prompt_version = prompt_version
        self.max_evidence_characters = max_evidence_characters
        self.top_k = top_k
        self.min_score = min_score
        self.trace_sink = trace_sink

    def score(
        self,
        scope: SearchScope,
        resume_id: str,
        job_version_id: str,
        resume_summary: str,
        job_text: str,
    ) -> Optional[SemanticProjectScore]:
        if not self.enabled:
            return None
        resume_sources = frozenset(
            item for item in scope.authorized_sources if item[0] == "resume" and item[1] == resume_id
        )
        job_sources = frozenset(
            item for item in scope.authorized_sources if item[0] == "job_version" and item[1] == job_version_id
        )
        if not resume_sources or not job_sources:
            self._fallback("insufficient_evidence")
            return None
        pair_scope = SearchScope(
            scope.tenant_id,
            frozenset({"resume", "job_version"}),
            resume_sources | job_sources,
        )
        try:
            resume_query = self.embedder.embed_query(job_text)
            job_query = self.embedder.embed_query(resume_summary)
            resume_hits = self.source_index.search(
                SearchScope(scope.tenant_id, frozenset({"resume"}), resume_sources),
                resume_query,
                self.embedder.model_name,
                self.top_k,
                self.min_score,
            )
            job_hits = self.source_index.search(
                SearchScope(scope.tenant_id, frozenset({"job_version"}), job_sources),
                job_query,
                self.embedder.model_name,
                self.top_k,
                self.min_score,
            )
        except Exception:
            self._fallback("retrieval_unavailable")
            return None
        if not resume_hits or not job_hits:
            self._fallback("insufficient_evidence")
            return None
        hits = resume_hits + job_hits
        evidence, prompt_citation_ids = format_evidence_with_citation_ids(
            hits,
            self.max_evidence_characters,
        )
        request = ModelRequest(
            operation="semantic_project_match",
            prompt_version=self.prompt_version,
            system=(
                "评估候选人项目经历与岗位职责的语义匹配度，分数限定在 0 到 100。"
                "必须分别引用至少一条 resume 和 job 证据，不得依据未提供的信息。"
            ),
            user=evidence,
            schema=SemanticProjectScore,
        )
        started = time.perf_counter()
        try:
            response = self.model.generate(request)
        except ModelGatewayError as error:
            self._trace_failed(scope.tenant_id, resume_id, job_version_id, request, error, started)
            return None
        except Exception:
            fallback_error = ModelGatewayError("unexpected_model_error", retryable=False)
            self._trace_failed(scope.tenant_id, resume_id, job_version_id, request, fallback_error, started)
            return None
        requested_ids = frozenset(response.value.resume_citation_ids + response.value.job_citation_ids)
        try:
            active_hits = self.source_index.resolve_active_citations(
                pair_scope,
                requested_ids & prompt_citation_ids,
            )
        except Exception:
            active_hits = []
        validated = validate_semantic_score(
            response.value,
            {
                hit.citation_id: hit.source_type
                for hit in active_hits
                if hit.citation_id in prompt_citation_ids and hit.citation_id in requested_ids
            },
        )
        if validated is None:
            record("citation.rejection", {"operation": "semantic_project_match", "error.code": "invalid_citation"})
            self._fallback("invalid_citation")
        if self.trace_sink is not None:
            try:
                self.trace_sink.succeeded(
                    scope.tenant_id,
                    "semantic_match",
                    resume_id,
                    [resume_id, job_version_id],
                    request,
                    response,
                    fallback_reason=None if validated is not None else "invalid_semantic_citations",
                )
            except Exception:
                pass
        return validated

    def _trace_failed(self, tenant_id, resume_id, job_version_id, request, error, started):
        self._fallback(error.code)
        if self.trace_sink is not None:
            try:
                self.trace_sink.failed(
                    tenant_id,
                    "semantic_match",
                    resume_id,
                    [resume_id, job_version_id],
                    request,
                    error,
                    (time.perf_counter() - started) * 1000,
                )
            except Exception:
                pass

    @staticmethod
    def _fallback(code):
        record("model.fallback", {"operation": "semantic_project_match", "outcome": "fallback", "error.code": code})
