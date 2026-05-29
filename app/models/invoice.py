"""
Invoice, Vendor, and InvoiceLineItem ORM models.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey,
    Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.custom_types import TextArray

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.processing_job import ProcessingJob


class Vendor(UUIDPrimaryKeyMixin, Base):
    """Normalized vendor registry."""
    __tablename__ = "vendors"

    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_names: Mapped[list[str]] = mapped_column(
        TextArray, nullable=False, default=list
    )
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("canonical_name", name="uq_vendor_canonical_name"),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    invoices: Mapped[list["Invoice"]] = relationship("Invoice", back_populates="vendor")

    def __repr__(self) -> str:
        return f"<Vendor id={self.id} name={self.canonical_name!r}>"


class Invoice(UUIDPrimaryKeyMixin, Base):
    """Extracted Invoice Data."""
    __tablename__ = "invoices"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id"), nullable=False
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )

    # ── Extracted core fields ──────────────────────────────────────────────────
    invoice_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Financial breakdown
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Raw extracted text for auditing
    raw_vendor_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_date_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_total_text: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Parser metadata
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Page ranges (if multi-invoice PDF)
    page_range_start: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    page_range_end: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    invoice_index: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="chk_invoice_confidence_range"),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    document: Mapped["Document"] = relationship("Document")
    processing_job: Mapped["ProcessingJob"] = relationship("ProcessingJob", back_populates="invoice")
    vendor: Mapped["Vendor | None"] = relationship("Vendor", back_populates="invoices")
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        "InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} number={self.invoice_number} total={self.total_amount}>"


class InvoiceLineItem(UUIDPrimaryKeyMixin, Base):
    """Individual line items within an invoice."""
    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit_of_measure: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("invoice_id", "line_number", name="uq_invoice_line_item_order"),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="line_items")

    def __repr__(self) -> str:
        return f"<InvoiceLineItem id={self.id} invoice={self.invoice_id} line={self.line_number}>"
