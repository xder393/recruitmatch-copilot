"""Immutable, tenant-scoped job catalog business rules."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.exceptions import AuthorizationError, ConflictError, ResourceNotFoundError
from app.domain.enums import JobStatus, Role
from app.models.jobs import Job, JobTemplate, JobVersion
from app.repositories.jobs import JobRepository
from app.security.tokens import Principal

_MUTATING_ROLES = {Role.ADMIN, Role.RECRUITER}
_REQUIRED_PROFILE_KEYS = {"job_family", "level", "required_skills", "preferred_skills", "weights"}


class JobService:
    def __init__(self, session: Session):
        self.session = session
        self.jobs = JobRepository(session)

    def list_templates(self) -> List[JobTemplate]:
        return self.jobs.list_templates()

    def create_job(
        self,
        principal: Principal,
        title: str,
        jd_text: str,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Job:
        self._require_mutation(principal)
        job = Job(
            tenant_id=principal.tenant_id,
            title=title.strip(),
            status=JobStatus.DRAFT,
            current_version=1,
            versions=[
                JobVersion(
                    version=1,
                    jd_text=jd_text.strip(),
                    profile=profile or {},
                    created_by=principal.user_id,
                )
            ],
        )
        self.jobs.add(job)
        self.session.commit()
        return self.get_job(principal, job.id)

    def update_job(
        self,
        principal: Principal,
        job_id: str,
        *,
        title: Optional[str] = None,
        jd_text: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Job:
        self._require_mutation(principal)
        job = self._owned_job(principal, job_id)
        latest = job.versions[-1]
        job.current_version += 1
        if title is not None:
            job.title = title.strip()
        job.versions.append(
            JobVersion(
                version=job.current_version,
                jd_text=latest.jd_text if jd_text is None else jd_text.strip(),
                profile=latest.profile if profile is None else profile,
                created_by=principal.user_id,
            )
        )
        self.session.commit()
        return self.get_job(principal, job.id)

    def activate_job(self, principal: Principal, job_id: str) -> Job:
        self._require_mutation(principal)
        job = self._owned_job(principal, job_id)
        latest = job.versions[-1]
        if not latest.jd_text or not _REQUIRED_PROFILE_KEYS.issubset(latest.profile):
            raise ConflictError("岗位画像不完整，无法发布")
        job.status = JobStatus.ACTIVE
        self.session.commit()
        return self.get_job(principal, job.id)

    def deactivate_job(self, principal: Principal, job_id: str) -> Job:
        self._require_mutation(principal)
        job = self._owned_job(principal, job_id)
        job.status = JobStatus.INACTIVE
        self.session.commit()
        return self.get_job(principal, job.id)

    def get_job(self, principal: Principal, job_id: str) -> Job:
        return self._owned_job(principal, job_id)

    def list_jobs(self, principal: Principal) -> List[Job]:
        return self.jobs.list(principal.tenant_id)

    def _owned_job(self, principal: Principal, job_id: str) -> Job:
        job = self.jobs.get(principal.tenant_id, job_id)
        if job is None:
            raise ResourceNotFoundError("岗位不存在")
        return job

    @staticmethod
    def _require_mutation(principal: Principal) -> None:
        if principal.role not in _MUTATING_ROLES:
            raise AuthorizationError("无权修改岗位")
