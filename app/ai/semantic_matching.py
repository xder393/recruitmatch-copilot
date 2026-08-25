"""Bounded semantic project-fit scoring that requires two-sided citations."""

from __future__ import annotations

from typing import Dict, List, Optional
import time

from pydantic import BaseModel, Field

from app.ai.citations import format_evidence
from app.ai.contracts import ModelRequest, StructuredModel
from app.ai.gateway import ModelGatewayError
from app.retrieval.indexing import EmbeddingAdapter
from app.retrieval.ports import RecruitingVectorIndex, SearchScope


class SemanticProjectScore(BaseModel):
    score: float
    rationale: str
    resume_citation_ids: List[str] = Field(default_factory=list)
    job_citation_ids: List[str] = Field(default_factory=list)


def validate_semantic_score(
    score: SemanticProjectScore,
    authorized_sources: Dict[str, str],
) -> Optional[SemanticProjectScore]:
    if not score.resume_citation_ids or not score.job_citation_ids:
        return None
    if any(authorized_sources.get(item) != "resume" for item in score.resume_citation_ids):
        return None
    if any(authorized_sources.get(item) != "job_version" for item in score.job_citation_ids):
        return None
    return score.model_copy(update={"score": min(max(float(score.score), 0.0), 1.0)})


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
        trace_sink=None,
    ):
        self.model = model
        self.source_index = source_index
        self.embedder = embedder
        self.enabled = enabled
        self.prompt_version = prompt_version
        self.max_evidence_characters = max_evidence_characters
        self.trace_sink = trace_sink

    def score(
        self,
        scope: SearchScope,
        resume_id: str,
        job_version_id: str,
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
            return None
        try:
            query = self.embedder.embed_query("candidate project evidence and job responsibilities")
            resume_hits = self.source_index.search(
                SearchScope(scope.tenant_id, frozenset({"resume"}), resume_sources),
                query,
                self.embedder.model_name,
                6,
                -1.0,
            )
            job_hits = self.source_index.search(
                SearchScope(scope.tenant_id, frozenset({"job_version"}), job_sources),
                query,
                self.embedder.model_name,
                6,
                -1.0,
            )
        except Exception:
            return None
        if not resume_hits or not job_hits:
            return None
        hits = resume_hits + job_hits
        request = ModelRequest(
            operation="semantic_project_match",
            prompt_version=self.prompt_version,
            system=(
                "评估候选人项目经历与岗位职责的语义匹配度，分数限定在 0 到 1。"
                "必须分别引用至少一条 resume 和 job 证据，不得依据未提供的信息。"
            ),
            user=format_evidence(hits, self.max_evidence_characters),
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
        validated = validate_semantic_score(
            response.value,
            {hit.citation_id: hit.source_type for hit in hits},
        )
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
