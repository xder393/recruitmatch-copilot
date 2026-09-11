"""Tenant-scoped recruiting knowledge endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from app.api.v1.deps import (
    get_current_principal,
    get_job_repository,
    get_knowledge_repository,
    get_resume_repository,
    get_unit_of_work,
)
from app.models.knowledge import KnowledgeDocument
from app.security.tokens import Principal
from app.repositories.unit_of_work import RecruitingUnitOfWork
from app.repositories.ports import JobRepository, KnowledgeRepository, ResumeRepository
from app.services.knowledge_documents import KnowledgeDocumentService
from app.services.source_index_backfill import SourceIndexBackfillService

router = APIRouter(prefix="/knowledge-documents", tags=["RecruitMatch Knowledge"])


class KnowledgeDocumentResponse(BaseModel):
    id: str
    document_type: str
    original_filename: str
    media_type: str
    size_bytes: int
    status: str
    active_generation: int
    error_code: Optional[str]
    error_message: Optional[str]


class KnowledgeDocumentListResponse(BaseModel):
    items: List[KnowledgeDocumentResponse]
    total: int


class SourceBackfillResponse(BaseModel):
    resumes_indexed: int
    job_versions_indexed: int
    failed: int


def _response(document: KnowledgeDocument) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document.id,
        document_type=document.document_type,
        original_filename=document.original_filename,
        media_type=document.media_type,
        size_bytes=document.size_bytes,
        status=document.status,
        active_generation=document.active_generation,
        error_code=document.error_code,
        error_message=document.error_message,
    )


def _service(
    request: Request,
    repository: KnowledgeRepository,
    uow: RecruitingUnitOfWork,
) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(
        repository,
        request.app.state.knowledge_artifact_store,
        request.app.state.knowledge_dispatcher,
        uow=uow,
    )


@router.post("", response_model=KnowledgeDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    try:
        document, _ = await run_in_threadpool(
            _service(request, repository, uow).upload,
            principal,
            document_type,
            file.filename or "document",
            file.content_type or "application/octet-stream",
            file.file,
        )
    finally:
        await file.close()
    return _response(document)


@router.get("", response_model=KnowledgeDocumentListResponse)
def list_documents(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    items = [_response(item) for item in _service(request, repository, uow).list(principal)]
    return KnowledgeDocumentListResponse(items=items, total=len(items))


@router.post("/rebuild-sources", response_model=SourceBackfillResponse)
def rebuild_sources(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    resumes: ResumeRepository = Depends(get_resume_repository),
    jobs: JobRepository = Depends(get_job_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return SourceIndexBackfillService(
        resumes,
        request.app.state.source_indexer,
        jobs,
        uow=uow,
    ).rebuild(principal)


@router.get("/{document_id}", response_model=KnowledgeDocumentResponse)
def get_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _response(_service(request, repository, uow).get(principal, document_id))


@router.post("/{document_id}/reindex", response_model=KnowledgeDocumentResponse)
def reindex_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _response(_service(request, repository, uow).reindex(principal, document_id))


@router.post("/{document_id}/deactivate", response_model=KnowledgeDocumentResponse)
def deactivate_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    return _response(_service(request, repository, uow).deactivate(principal, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    request: Request,
    confirm_tenant_history_redaction: bool = False,
    principal: Principal = Depends(get_current_principal),
    repository: KnowledgeRepository = Depends(get_knowledge_repository),
    uow: RecruitingUnitOfWork = Depends(get_unit_of_work),
):
    """Privacy-delete Knowledge and all existing tenant match content after explicit acknowledgement."""
    _service(request, repository, uow).delete(
        principal, document_id, confirm_tenant_history_redaction=confirm_tenant_history_redaction
    )
