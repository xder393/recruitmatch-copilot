"""Bounded semantic project-fit scoring that requires two-sided citations."""
from __future__ import annotations

from typing import List, Optional, Set

from pydantic import BaseModel, Field

from app.ai.citations import format_evidence
from app.ai.contracts import ModelRequest, StructuredModel


class SemanticProjectScore(BaseModel):
    score: float
    rationale: str
    resume_citation_ids: List[str] = Field(default_factory=list)
    job_citation_ids: List[str] = Field(default_factory=list)


def validate_semantic_score(
    score: SemanticProjectScore,
    authorized_ids: Set[str],
) -> Optional[SemanticProjectScore]:
    citation_ids = score.resume_citation_ids + score.job_citation_ids
    if not score.resume_citation_ids or not score.job_citation_ids:
        return None
    if any(item not in authorized_ids for item in citation_ids):
        return None
    return score.model_copy(update={"score": min(max(float(score.score), 0.0), 1.0)})


class SemanticMatcher:
    def __init__(
        self,
        model: StructuredModel,
        source_index,
        *,
        enabled: bool = True,
        prompt_version: str = "semantic-project-v1",
        max_evidence_characters: int = 8000,
    ):
        self.model = model
        self.source_index = source_index
        self.enabled = enabled
        self.prompt_version = prompt_version
        self.max_evidence_characters = max_evidence_characters

    def score(self, tenant_id: str, resume_id: str, job_version_id: str) -> Optional[SemanticProjectScore]:
        if not self.enabled:
            return None
        resume_hits = self.source_index.source_chunks(tenant_id, "resume", resume_id)
        job_hits = self.source_index.source_chunks(tenant_id, "job", job_version_id)
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
        try:
            output = self.model.generate(request).value
        except Exception:
            return None
        return validate_semantic_score(output, {hit.citation_id for hit in hits})
