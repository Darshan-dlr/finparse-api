"""
Document model — immutable raw file registry.
Every upload creates one row here. Never deleted (soft delete only).
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING
import enum

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime,
    Enum as SAEnum, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.processing_job import ProcessingJob
    from app.models.document_tag import DocumentTag


class DocumentType(str, enum.Enum):
    INVOICE = "invoice"
    BANK_STATEMENT = "bank_statement"


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Immutable record of every uploaded file.
    Acts as the root entity — everything else hangs off this.
    """
    __tablename__ = "documents"

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        SAEnum(DocumentType, name="document_type_enum"), nullable=False
    )
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'pdf' | 'csv'
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(is_deleted = FALSE AND deleted_at IS NULL) OR "
            "(is_deleted = TRUE AND deleted_at IS NOT NULL)",
            name="chk_documents_deleted_at_consistency",
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob", back_populates="document", cascade="all, delete-orphan"
    )
    tags: Mapped[list["DocumentTag"]] = relationship(
        "DocumentTag", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} type={self.document_type} name={self.original_name!r}>"
