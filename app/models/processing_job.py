"""
ProcessingJob model — async parsing lifecycle tracker.
One per upload (or more if allow_reprocess=true).
"""
import uuid
import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey,
    Integer, SmallInteger, String, Text, func, JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.custom_types import IntegerArray

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.bank_statement import BankStatement
    from app.models.invoice import Invoice


class JobStatus(str, enum.Enum):
    PENDING = "pending"         # Uploaded, worker hasn't picked it up yet
    PROCESSING = "processing"   # Worker is actively parsing
    COMPLETED = "completed"     # Fully parsed, all fields extracted
    FAILED = "failed"           # Parsing failed entirely
    PARTIAL = "partial"         # Parsed with warnings (missing fields, guessed values)


class ProcessingJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "processing_jobs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status_enum"),
        nullable=False,
        default=JobStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Error tracking ─────────────────────────────────────────────────────────
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        comment="Structured: {stage, field, raw_value, traceback}"
    )

    # ── Retry tracking ─────────────────────────────────────────────────────────
    retry_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)

    # ── Non-fatal warnings ─────────────────────────────────────────────────────
    warnings: Mapped[list[dict] | None] = mapped_column(
        JSON, nullable=True,
        comment="Array: [{field, message}] — non-fatal issues during parsing"
    )

    # ── Parser metadata ────────────────────────────────────────────────────────
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_reprocess: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="TRUE when triggered by allow_reprocess=true"
    )

    # ── PDF-specific fields (from edge case analysis) ──────────────────────────
    pdf_password_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pdf_encryption_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ocr_engine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ocr_confidence_avg: Mapped[float | None] = mapped_column(nullable=True)
    scanned_pages: Mapped[list[int] | None] = mapped_column(
        IntegerArray, nullable=True,
        comment="Page numbers (1-indexed) that required OCR"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    document: Mapped["Document"] = relationship("Document", back_populates="processing_jobs")
    bank_statement: Mapped["BankStatement | None"] = relationship(
        "BankStatement", back_populates="processing_job", uselist=False
    )
    invoice: Mapped["Invoice | None"] = relationship(
        "Invoice", back_populates="processing_job", uselist=False
    )

    def __repr__(self) -> str:
        return f"<ProcessingJob id={self.id} status={self.status} doc={self.document_id}>"
