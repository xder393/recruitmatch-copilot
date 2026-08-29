"""Tenant-scoped recommendation orchestration."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.exceptions import ConflictError, ResourceNotFoundError
from app.domain.enums import MatchStatus
from app.matching.engine import MatchingEngine
from app.matching.hybrid import HybridMatchingEngine, HybridTenantContext
from app.matching.schemas import CandidateJob
from app.models.matching import MatchResult, MatchRun
from app.repositories.ports import MatchingRepository
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.resumes.schemas import ResumeProfile
from app.security.tokens import Principal
from app.retrieval.indexing import EmbeddingAdapter
from app.retrieval.ports import RecruitingVectorIndex, SearchScope


class MatchingService:
    def __init__(
        self,
        repository: MatchingRepository,
        engine: MatchingEngine | None = None,
        hybrid_engine: HybridMatchingEngine | None = None,
        explanation_service=None,
        source_index: RecruitingVectorIndex | None = None,
        embedder: EmbeddingAdapter | None = None,
        retrieval_top_k: int = 6,
        retrieval_min_score: float = 0.35,
        *,
        uow: RecruitingUnitOfWork,
    ):
        self.repository = repository
        self.uow = uow
        self.engine = engine or MatchingEngine()
        self.hybrid_engine = hybrid_engine
        self.explanation_service = explanation_service
        self.source_index = source_index
        self.embedder = embedder
        self.retrieval_top_k = retrieval_top_k
        self.retrieval_min_score = retrieval_min_score

    def run(self, principal: Principal, resume_id: str, mode: str = "rules-v1") -> MatchRun:
        if mode not in {"rules-v1", "hybrid-v1"}:
            raise ConflictError("不支持的匹配算法")
        if mode == "hybrid-v1" and self.hybrid_engine is None:
            raise ConflictError("混合匹配服务未配置")
        resume = self.repository.get_succeeded_resume(principal.tenant_id, resume_id)
        if resume is None:
            raise ResourceNotFoundError("已完成解析的简历不存在")
        jobs = self.repository.active_jobs(principal.tenant_id)
        if not jobs:
            raise ConflictError("没有已发布岗位可供匹配")

        candidates = []
        source_versions: dict[str, str] = {}
        for job in jobs:
            version = next((item for item in job.versions if item.version == job.current_version), None)
            if version is None:
                continue
            source_versions[version.id] = str(version.version)
            candidates.append(
                CandidateJob(
                    job_id=job.id,
                    job_version_id=version.id,
                    title=job.title,
                    jd_text=version.jd_text,
                    profile=version.profile,
                )
            )
        if not candidates:
            raise ConflictError("已发布岗位缺少有效版本")

        run = MatchRun(
            tenant_id=principal.tenant_id,
            resume_id=resume.id,
            created_by=principal.user_id,
            status=MatchStatus.RUNNING,
            algorithm_version=mode,
            prompt_version=self._prompt_version() if mode == "hybrid-v1" else "none",
        )
        self.repository.add_run(run)
        profile = ResumeProfile.model_validate(resume.profile)
        if mode == "hybrid-v1":
            search_scope = self._search_scope(principal.tenant_id, resume, candidates, source_versions)
            recommendations = self.hybrid_engine.rank(  # type: ignore[union-attr]
                profile,
                candidates,
                HybridTenantContext(resume.id, search_scope),
                top_k=3,
            )
            recommendations = self._add_grounded_guidance(
                search_scope,
                resume.id,
                candidates,
                recommendations,
            )
        else:
            recommendations = self.engine.rank(profile, candidates, top_k=3)
        for rank, item in enumerate(recommendations, start=1):
            run.results.append(
                MatchResult(
                    job_version_id=item.job_version_id,
                    rank=rank,
                    total_score=item.total_score,
                    dimension_scores=item.dimension_scores.model_dump(mode="json"),
                    matched_items=item.matched_items,
                    missing_items=item.missing_items,
                    uncertain_items=item.uncertain_items,
                    evidence=[evidence.model_dump(mode="json") for evidence in item.evidence],
                    risk_flags=item.risk_flags,
                    summary=item.summary,
                    rule_score=getattr(item, "rule_score", None),
                    semantic_score=getattr(item, "semantic_score", None),
                    grounding_status=getattr(item, "grounding_status", None),
                    fallback_reason=getattr(item, "fallback_reason", None),
                    citations=[
                        citation if isinstance(citation, dict) else {"id": citation}
                        for citation in getattr(item, "citations", [])
                    ],
                    grounded_explanation=getattr(item, "grounded_explanation", {}),
                    interview_questions=getattr(item, "interview_questions", []),
                )
            )
        run.status = MatchStatus.SUCCEEDED
        run.completed_at = datetime.now(timezone.utc)
        self.uow.commit()
        loaded = self.repository.get_run(principal.tenant_id, run.id)
        if loaded is None:
            raise RuntimeError("match run disappeared after commit")
        return loaded

    def _prompt_version(self) -> str:
        semantic = getattr(
            getattr(self.hybrid_engine, "semantic_matcher", None),
            "prompt_version",
            "semantic-project-v1",
        )
        explanation = getattr(self.explanation_service, "prompt_version", None)
        return "+".join(item for item in [semantic, explanation] if item)

    def get_run(self, principal: Principal, run_id: str) -> MatchRun:
        run = self.repository.get_run(principal.tenant_id, run_id)
        if run is None:
            raise ResourceNotFoundError("匹配任务不存在")
        return run

    def _search_scope(self, tenant_id, resume, candidates, source_versions: dict[str, str]) -> SearchScope:
        authorized = {("resume", resume.id, resume.sha256)}
        authorized.update(
            ("job_version", item.job_version_id, source_versions[item.job_version_id]) for item in candidates
        )
        try:
            documents = self.uow.knowledge.list_documents(tenant_id)
        except Exception:
            documents = []
        authorized.update(
            ("knowledge_document", item.id, item.checksum)
            for item in documents
            if item.status not in {"inactive", "deleted"} and item.search_index_status == "ready"
        )
        return SearchScope(tenant_id, frozenset(item[0] for item in authorized), frozenset(authorized))

    def _add_grounded_guidance(self, search_scope, resume_id, candidates, recommendations):
        if self.explanation_service is None or self.source_index is None or self.embedder is None:
            return recommendations
        by_version = {item.job_version_id: item for item in candidates}
        enriched = []
        for item in recommendations:
            job = by_version[item.job_version_id]
            try:
                selected = frozenset(
                    source
                    for source in search_scope.authorized_sources
                    if source[0] == "knowledge_document"
                    or (source[0] == "resume" and source[1] == resume_id)
                    or (source[0] == "job_version" and source[1] == item.job_version_id)
                )
                scope = SearchScope(
                    search_scope.tenant_id,
                    frozenset(source[0] for source in selected),
                    selected,
                )
                hits = self.source_index.search(
                    scope,
                    self.embedder.embed_query(job.jd_text),
                    self.embedder.model_name,
                    self.retrieval_top_k,
                    self.retrieval_min_score,
                )
                explanation = self.explanation_service.generate(
                    scope,
                    resume_id,
                    item.job_version_id,
                    {
                        "matched": item.matched_items,
                        "missing": item.missing_items,
                        "uncertain": item.uncertain_items,
                    },
                    hits,
                )
            except Exception:
                enriched.append(
                    item.model_copy(
                        update={
                            "grounding_status": "rules_fallback",
                            "fallback_reason": item.fallback_reason or "guidance_retrieval_unavailable",
                        }
                    )
                )
                continue
            used_ids = set(item.citations)
            used_ids.update(explanation.citations)
            citations = [self._citation_payload(hit) for hit in hits if hit.citation_id in used_ids]
            guidance = explanation.model_dump(mode="json", exclude={"citations", "interview_questions"})
            update = {
                "citations": citations,
                "grounded_explanation": guidance,
                "interview_questions": [
                    question.model_dump(mode="json") for question in explanation.interview_questions
                ],
                "grounding_status": (
                    "rules_fallback" if getattr(item, "semantic_score", None) is None else explanation.grounding_status
                ),
                "fallback_reason": (
                    item.fallback_reason
                    or (None if explanation.grounding_status == "grounded" else explanation.grounding_status)
                ),
            }
            if explanation.summary is not None:
                update["summary"] = explanation.summary.text
            enriched.append(item.model_copy(update=update))
        return enriched

    @staticmethod
    def _citation_payload(hit):
        return {
            "id": hit.citation_id,
            "source_type": hit.source_type,
            "source_id": hit.source_id,
            "content": hit.content,
            "start_offset": hit.start_offset,
            "end_offset": hit.end_offset,
            "page_number": hit.page_number,
        }

    def latest_for_resume(self, principal: Principal, resume_id: str) -> MatchRun:
        run = self.repository.latest_for_resume(principal.tenant_id, resume_id)
        if run is None:
            raise ResourceNotFoundError("该简历尚无匹配结果")
        return run
