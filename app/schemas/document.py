"""
Pydantic schemas for Documents.
"""
from datetime import datetime
import uuid

from app.models.document import DocumentType
from app.schemas.common import BaseSchema


class DocumentRead(BaseSchema):
    id: uuid.UUID
    filename: str
    original_name: str
    document_type: DocumentType
    file_type: str
    file_size_bytes: int
    checksum_sha256: str
    uploaded_by: str
    uploaded_at: datetime


class DocumentUploadResponse(BaseSchema):
    document_id: str
    job_id: str
    status: str
    is_reprocess: bool
    message: str


class DocumentListResponse(BaseSchema):
    items: list[DocumentRead]
    count: int

