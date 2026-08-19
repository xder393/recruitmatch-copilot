"""Tenant-isolated resume upload and lifecycle endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db
from app.api.v1.schemas import ResumeListResponse, ResumeResponse
from app.models.resumes import Resume
from app.security.tokens import Principal
from app.services.resumes import ResumeService

router = APIRouter(prefix="/resumes", tags=["RecruitMatch Resumes"])


def _service(request: Request, session: Session) -> ResumeService:
    return ResumeService(session, request.app.state.artifact_store, request.app.state.task_dispatcher)


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
    )


@router.post("", response_model=ResumeResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_resume(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    content = await file.read(10 * 1024 * 1024 + 1)
    resume, created = _service(request, session).upload(
        principal,
        file.filename or "resume",
        file.content_type or "application/octet-stream",
        content,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return _response(resume)


@router.get("", response_model=ResumeListResponse)
def list_resumes(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    items = [_response(item) for item in _service(request, session).list(principal)]
    return ResumeListResponse(items=items, total=len(items))


@router.get("/{resume_id}", response_model=ResumeResponse)
def get_resume(
    resume_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _response(_service(request, session).get(principal, resume_id))


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    _service(request, session).delete(principal, resume_id)
