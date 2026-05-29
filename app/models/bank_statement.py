"""
BankStatement and BankTransaction ORM models.
"""
import uuid
import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum as SAEnum,
    ForeignKey, Integer, Numeric, SmallInteger, String, Text, func, JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.processing_job import ProcessingJob


class TransactionType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    TRANSFER = "transfer"
    FEE = "fee"
    INTEREST = "interest"
    UNKNOWN = "unknown"


class BankStatement(UUIDPrimaryKeyMixin, Base):
    """Statement-level metadata extracted from a CSV bank statement."""
    __tablename__ = "bank_statements"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id"), nullable=False
    )

    # ── Extracted fields ───────────────────────────────────────────────────────
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
        comment="MASKED — last 4 digits only. e.g. ****4321"
    )
    account_holder: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # TODO: Add base_currency + exchange_rate for cross-currency normalization
    # base_currency: Mapped[str | None]
    # exchange_rate: Mapped[Decimal | None]

    statement_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    statement_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # ── CSV format metadata (from edge case analysis) ──────────────────────────
    raw_headers: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        comment="Original CSV column names as-is, for debugging format mismatches"
    )
    detected_format: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        comment="Detected bank format: HDFC_V2, ICICI_STANDARD, GENERIC, etc."
    )
    detected_delimiter: Mapped[str | None] = mapped_column(String(1), nullable=True)
    detected_encoding: Mapped[str | None] = mapped_column(String(30), nullable=True)
    total_rows_parsed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_rows_skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)

    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    document: Mapped["Document"] = relationship("Document")
    processing_job: Mapped["ProcessingJob"] = relationship(
        "ProcessingJob", back_populates="bank_statement"
    )
    transactions: Mapped[list["BankTransaction"]] = relationship(
        "BankTransaction", back_populates="bank_statement", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BankStatement id={self.id} bank={self.bank_name} currency={self.currency}>"


class BankTransaction(UUIDPrimaryKeyMixin, Base):
    """Individual transaction row from a bank statement CSV."""
    __tablename__ = "bank_transactions"

    bank_statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_statements.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Core transaction fields ────────────────────────────────────────────────
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_description: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Original CSV text before cleaning"
    )
    reference_number: Mapped[str | None] = mapped_column(String(255), nullable=True)

    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, name="transaction_type_enum"),
        nullable=False,
        default=TransactionType.UNKNOWN,
    )

    # ── Amount — always positive; direction is explicit ────────────────────────
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False,
        comment="Always positive. Use direction column for sign."
    )
    direction: Mapped[str] = mapped_column(
        String(1), nullable=False,
        comment="C = Credit (money in), D = Debit (money out)"
    )
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(
        String(3), nullable=True,
        comment="Per-row currency for multi-currency statements"
    )

    # TODO: Add cross-currency normalization columns
    # base_amount: Mapped[Decimal | None]
    # exchange_rate: Mapped[Decimal | None]

    # ── Parser metadata ────────────────────────────────────────────────────────
    row_index: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Original CSV row number (1-indexed) for debugging"
    )
    parse_warnings: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True,
        comment="Row-level warnings: ['date guessed', 'amount format unusual']"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("amount >= 0", name="chk_bank_tx_amount_positive"),
        CheckConstraint("direction IN ('C', 'D')", name="chk_bank_tx_direction"),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    bank_statement: Mapped["BankStatement"] = relationship(
        "BankStatement", back_populates="transactions"
    )

    def __repr__(self) -> str:
        return (
            f"<BankTransaction id={self.id} date={self.transaction_date} "
            f"amount={self.direction}{self.amount}>"
        )
