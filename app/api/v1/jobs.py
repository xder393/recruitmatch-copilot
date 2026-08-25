"""Tenant-scoped job catalog endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Request, status
from app.api.v1.deps import get_current_principal, get_job_repository, get_unit_of_work
from app.api.v1.schemas import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobTemplateResponse,
    JobUpdateRequest,
    JobVersionResponse,
)
from app.models.jobs import Job, JobTemplate
from app.security.tokens import Principal
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.repositories.ports import JobRepository
from app.services.jobs import JobService

router = APIRouter(tags=["RecruitMatch Jobs"])


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        title=job.title,
        status=job.status,
        current_version=job.current_version,
        versions=[
            JobVersionResponse(
                id=version.id,
                version=version.version,
                jd_text=version.jd_text,
                profile=version.profile,
                created_by=version.created_by,
                search_index_status=version.search_index_status,
                search_index_error=version.search_index_error,
            )
            for version in job.versions
        ],
    )


def _template_response(template: JobTemplate) -> JobTemplateResponse:
    return JobTemplateResponse(
        id=template.id,
        slug=template.slug,
        title=template.title,
        jd_text=template.jd_text,
        profile=template.profile,
    )


@router.get("/job-templates", response_model=List[JobTemplateResponse])
def list_templates(
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    del principal
    return [_template_response(item) for item in JobService(jobs, uow=uow).list_templates()]


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobCreateRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _job_response(
        JobService(jobs, request.app.state.source_indexer, uow=uow).create_job(
            principal, payload.title, payload.jd_text, payload.profile
        )
    )


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    items = [_job_response(item) for item in JobService(jobs, uow=uow).list_jobs(principal)]
    return JobListResponse(items=items, total=len(items))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _job_response(JobService(jobs, uow=uow).get_job(principal, job_id))


@router.put("/jobs/{job_id}", response_model=JobResponse)
def update_job(
    job_id: str,
    payload: JobUpdateRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _job_response(
        JobService(jobs, request.app.state.source_indexer, uow=uow).update_job(
            principal,
            job_id,
            title=payload.title,
            jd_text=payload.jd_text,
            profile=payload.profile,
        )
    )


@router.post("/jobs/{job_id}/activate", response_model=JobResponse)
def activate_job(
    job_id: str,
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _job_response(JobService(jobs, uow=uow).activate_job(principal, job_id))


@router.post("/jobs/{job_id}/deactivate", response_model=JobResponse)
def deactivate_job(
    job_id: str,
    principal: Principal = Depends(get_current_principal),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _job_response(JobService(jobs, uow=uow).deactivate_job(principal, job_id))
