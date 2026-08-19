"""Tenant-scoped recruiting knowledge endpoints."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_principal, get_db
from app.models.knowledge import KnowledgeDocument
from app.security.tokens import Principal
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


def _service(request: Request, session: Session) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(
        session,
        request.app.state.knowledge_artifact_store,
        request.app.state.knowledge_dispatcher,
    )


@router.post("", response_model=KnowledgeDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    content = await file.read(10 * 1024 * 1024 + 1)
    document, _ = _service(request, session).upload(
        principal,
        document_type,
        file.filename or "document",
        file.content_type or "application/octet-stream",
        content,
    )
    return _response(document)


@router.get("", response_model=KnowledgeDocumentListResponse)
def list_documents(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    items = [_response(item) for item in _service(request, session).list(principal)]
    return KnowledgeDocumentListResponse(items=items, total=len(items))


@router.post("/rebuild-sources", response_model=SourceBackfillResponse)
def rebuild_sources(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return SourceIndexBackfillService(session, request.app.state.knowledge_index).rebuild(principal)


@router.get("/{document_id}", response_model=KnowledgeDocumentResponse)
def get_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _response(_service(request, session).get(principal, document_id))


@router.post("/{document_id}/reindex", response_model=KnowledgeDocumentResponse)
def reindex_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _response(_service(request, session).reindex(principal, document_id))


@router.post("/{document_id}/deactivate", response_model=KnowledgeDocumentResponse)
def deactivate_document(
    document_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
):
    return _response(_service(request, session).deactivate(principal, document_id))
