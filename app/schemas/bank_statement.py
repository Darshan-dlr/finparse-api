"""
Pydantic schemas for BankStatements and BankTransactions.
"""
from datetime import date, datetime
from decimal import Decimal
import uuid
from typing import Any

from app.models.bank_statement import TransactionType
from app.schemas.common import BaseSchema


class BankTransactionRead(BaseSchema):
    id: uuid.UUID
    bank_statement_id: uuid.UUID
    transaction_date: date
    value_date: date | None
    description: str | None
    raw_description: str | None
    reference_number: str | None
    transaction_type: TransactionType
    amount: Decimal
    direction: str
    balance_after: Decimal | None
    currency: str | None
    row_index: int
    parse_warnings: list[str] | None
    created_at: datetime


class BankStatementRead(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    processing_job_id: uuid.UUID
    bank_name: str | None
    account_number: str | None
    account_holder: str | None
    currency: str | None
    statement_from: date | None
    statement_to: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    raw_headers: dict[str, Any] | None
    detected_format: str | None
    detected_delimiter: str | None
    detected_encoding: str | None
    total_rows_parsed: int | None
    total_rows_skipped: int | None
    extracted_at: datetime
    updated_at: datetime


class BankStatementWithTransactionsRead(BankStatementRead):
    transactions: list[BankTransactionRead]
