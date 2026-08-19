"""Tenant-scoped recommendation orchestration."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ResourceNotFoundError
from app.domain.enums import MatchStatus
from app.matching.engine import MatchingEngine
from app.matching.hybrid import HybridMatchingEngine, HybridTenantContext
from app.matching.schemas import CandidateJob
from app.models.matching import MatchResult, MatchRun
from app.repositories.matching import MatchingRepository
from app.resumes.schemas import ResumeProfile
from app.security.tokens import Principal


class MatchingService:
    def __init__(
        self,
        session: Session,
        engine: MatchingEngine | None = None,
        hybrid_engine: HybridMatchingEngine | None = None,
    ):
        self.session = session
        self.engine = engine or MatchingEngine()
        self.hybrid_engine = hybrid_engine
        self.repository = MatchingRepository(session)

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
        for job in jobs:
            version = next((item for item in job.versions if item.version == job.current_version), None)
            if version is None:
                continue
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
            prompt_version="semantic-project-v1" if mode == "hybrid-v1" else "none",
        )
        self.repository.add_run(run)
        profile = ResumeProfile.model_validate(resume.profile)
        if mode == "hybrid-v1":
            recommendations = self.hybrid_engine.rank(  # type: ignore[union-attr]
                profile,
                candidates,
                HybridTenantContext(principal.tenant_id, resume.id),
                top_k=3,
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
                )
            )
        run.status = MatchStatus.SUCCEEDED
        run.completed_at = datetime.now(timezone.utc)
        self.session.commit()
        loaded = self.repository.get_run(principal.tenant_id, run.id)
        if loaded is None:
            raise RuntimeError("match run disappeared after commit")
        return loaded

    def get_run(self, principal: Principal, run_id: str) -> MatchRun:
        run = self.repository.get_run(principal.tenant_id, run_id)
        if run is None:
            raise ResourceNotFoundError("匹配任务不存在")
        return run

    def latest_for_resume(self, principal: Principal, resume_id: str) -> MatchRun:
        run = self.repository.latest_for_resume(principal.tenant_id, resume_id)
        if run is None:
            raise ResourceNotFoundError("该简历尚无匹配结果")
        return run
