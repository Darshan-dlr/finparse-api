"""
Pydantic schemas for ProcessingJobs.
"""
from datetime import datetime
import uuid
from typing import Any

from app.models.processing_job import JobStatus
from app.schemas.common import BaseSchema


class ProcessingJobRead(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    status: JobStatus
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    error_detail: dict[str, Any] | None
    retry_count: int
    max_retries: int
    warnings: list[dict[str, Any]] | None
    parser_version: str | None
    is_reprocess: bool
    pdf_password_used: bool
    pdf_encryption_type: str | None
    ocr_used: bool
    ocr_engine: str | None
    ocr_confidence_avg: float | None
    scanned_pages: list[int] | None
    created_at: datetime
    updated_at: datetime
