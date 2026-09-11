"""Recruiter feedback validation and append-only persistence."""

from __future__ import annotations

from app.core.exceptions import ConflictError, ResourceNotFoundError
from app.domain.enums import FeedbackAction
from app.models.matching import Feedback
from app.repositories.ports import FeedbackRepository
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.security.tokens import Principal


class FeedbackService:
    def __init__(self, repository: FeedbackRepository, uow: RecruitingUnitOfWork):
        self.repository = repository
        self.uow = uow

    def submit(
        self,
        principal: Principal,
        result_id: str,
        action: FeedbackAction,
        reason: str | None = None,
        corrected_job_version_id: str | None = None,
    ) -> Feedback:
        self.uow.identities.lock_privacy_guard(principal.tenant_id)
        result = self.repository.get_result(principal.tenant_id, result_id)
        if result is None:
            raise ResourceNotFoundError("匹配结果不存在")
        if action is FeedbackAction.REASSIGN:
            if not corrected_job_version_id:
                raise ConflictError("改选岗位时必须提供岗位版本")
            if self.repository.get_job_version(principal.tenant_id, corrected_job_version_id) is None:
                raise ResourceNotFoundError("改选岗位版本不存在")
        else:
            corrected_job_version_id = None

        feedback = Feedback(
            tenant_id=principal.tenant_id,
            match_result_id=result.id,
            user_id=principal.user_id,
            action=action,
            corrected_job_version_id=corrected_job_version_id,
            reason=reason.strip() if reason else None,
        )
        self.repository.add(feedback)
        self.uow.commit()
        return feedback
