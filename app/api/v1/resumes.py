"""Tenant-isolated resume upload and lifecycle endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from starlette.concurrency import run_in_threadpool
from app.api.v1.deps import get_current_principal, get_resume_repository, get_unit_of_work
from app.api.v1.schemas import ResumeListResponse, ResumeResponse
from app.models.resumes import Resume
from app.security.tokens import Principal
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.repositories.ports import ResumeRepository
from app.services.resumes import ResumeService

router = APIRouter(prefix="/resumes", tags=["RecruitMatch Resumes"])


def _service(request: Request, resumes: ResumeRepository, uow: RecruitingUnitOfWork) -> ResumeService:
    return ResumeService(
        resumes,
        request.app.state.artifact_store,
        request.app.state.task_dispatcher,
        uow=uow,
    )


def _response(resume: Resume) -> ResumeResponse:
    return ResumeResponse(
        id=resume.id,
        original_filename=resume.original_filename,
        media_type=resume.media_type,
        size_bytes=resume.size_bytes,
        status=resume.status,
        profile=resume.profile,
        error_code=resume.error_code,
        error_message=resume.error_message,
        search_index_status=resume.search_index_status,
        search_index_error=resume.search_index_error,
    )


@router.post("", response_model=ResumeResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_resume(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    resumes: ResumeRepository = Depends(get_resume_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    try:
        resume, created = await run_in_threadpool(
            _service(request, resumes, uow).upload,
            principal,
            file.filename or "resume",
            file.content_type or "application/octet-stream",
            file.file,
        )
    finally:
        await file.close()
    if not created:
        response.status_code = status.HTTP_200_OK
    return _response(resume)


@router.get("", response_model=ResumeListResponse)
def list_resumes(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    resumes: ResumeRepository = Depends(get_resume_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    items = [_response(item) for item in _service(request, resumes, uow).list(principal)]
    return ResumeListResponse(items=items, total=len(items))


@router.get("/{resume_id}", response_model=ResumeResponse)
def get_resume(
    resume_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    resumes: ResumeRepository = Depends(get_resume_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _response(_service(request, resumes, uow).get(principal, resume_id))


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    resumes: ResumeRepository = Depends(get_resume_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    _service(request, resumes, uow).delete(principal, resume_id)
