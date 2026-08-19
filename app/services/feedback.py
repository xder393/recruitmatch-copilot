"""Recruiter feedback validation and append-only persistence."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ResourceNotFoundError
from app.domain.enums import FeedbackAction
from app.models.matching import Feedback
from app.repositories.feedback import FeedbackRepository
from app.security.tokens import Principal


class FeedbackService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = FeedbackRepository(session)

    def submit(
        self,
        principal: Principal,
        result_id: str,
        action: FeedbackAction,
        reason: str | None = None,
        corrected_job_version_id: str | None = None,
    ) -> Feedback:
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
        self.session.add(feedback)
        self.session.commit()
        self.session.refresh(feedback)
        return feedback
