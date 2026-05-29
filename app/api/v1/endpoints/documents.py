"""
Document upload and retrieval endpoints.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_user
from app.services.document_service import DocumentService
from app.core.logging import get_logger
from app.schemas.document import DocumentRead, DocumentUploadResponse, DocumentListResponse
from app.schemas.processing_job import ProcessingJobRead

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/upload",
    summary="Upload a bank statement (CSV) or invoice (PDF)",
    status_code=202,
    response_model=DocumentUploadResponse,
    responses={
        202: {"description": "Accepted — file queued for parsing"},
        400: {"description": "Empty file or upload incomplete"},
        409: {"description": "Duplicate file (pass ?allow_reprocess=true to re-parse)"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported or mismatched file type"},
        422: {"description": "Invalid file content (PDF encrypted, corrupted, etc.)"},
    },
)
async def upload_document(
    file: Annotated[UploadFile, File(description="PDF invoice or CSV bank statement")],
    pdf_password: Annotated[
        str | None,
        Form(description="Password for encrypted PDFs — never stored in DB"),
    ] = None,
    allow_reprocess: Annotated[
        bool,
        Query(description="Re-parse an already-uploaded file (same checksum)"),
    ] = False,
    current_user: Annotated[str, Depends(get_current_user)] = "system",
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a financial document for parsing.

    - **PDF invoices**: validated for encryption, page count, and scanned pages
    - **CSV bank statements**: fully parsed through the 5-stage pipeline
    - **XLSX files**: auto-converted to CSV before parsing

    **Duplicate handling**: By default returns `409 Conflict` if the same file
    was uploaded before. Pass `?allow_reprocess=true` to create a new parsing
    job on the existing document record.
    """
    service = DocumentService(db)
    result = await service.upload_and_enqueue(
        file=file,
        pdf_password=pdf_password,
        allow_reprocess=allow_reprocess,
        uploaded_by=current_user,
    )
    return result


@router.get(
    "/{document_id}",
    summary="Get document details and parsed data",
    response_model=DocumentRead,
)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    return await service.get_document(document_id)


@router.get(
    "/{document_id}/job",
    summary="Get the latest processing job status for a document",
    response_model=ProcessingJobRead,
)
async def get_job_status(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    return await service.get_latest_job(document_id)


@router.get(
    "/",
    summary="List and filter documents",
    response_model=DocumentListResponse,
)
async def list_documents(
    document_type: str | None = Query(None, description="invoice | bank_statement"),
    status: str | None = Query(None, description="pending | processing | completed | failed | partial"),
    currency: str | None = Query(None, description="ISO 4217 currency code, e.g. USD"),
    uploaded_after: str | None = Query(None, description="ISO date: 2026-01-01"),
    uploaded_before: str | None = Query(None, description="ISO date: 2026-12-31"),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    return await service.list_documents(
        document_type=document_type,
        status=status,
        currency=currency,
        uploaded_after=uploaded_after,
        uploaded_before=uploaded_before,
        limit=limit,
        offset=offset,
    )


@router.delete(
    "/{document_id}",
    summary="Soft-delete a document and all its parsed data",
    status_code=204,
)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    await service.soft_delete(document_id)

