from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from app.models.enums import TransactionType

@dataclass
class ParsedTransaction:
    """One fully-parsed transaction row."""
    row_index: int
    transaction_date: date
    value_date: date | None
    description: str | None
    raw_description: str | None
    reference_number: str | None
    transaction_type: TransactionType
    amount: Decimal
    direction: str          # 'C' | 'D'
    balance_after: Decimal | None
    currency: str | None
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class ColumnMapping:
    """Maps logical field names to actual CSV column indices."""
    date: int | None = None
    value_date: int | None = None
    description: int | None = None
    reference: int | None = None
    amount: int | None = None          # Single signed/unsigned amount column
    debit: int | None = None           # Separate debit column
    credit: int | None = None          # Separate credit column
    balance: int | None = None
    currency: int | None = None
    transaction_type: int | None = None


@dataclass
class ParsedBankStatement:
    """Final result from the full CSV parsing pipeline."""
    bank_name: str | None
    account_number: str | None          # MASKED
    account_holder: str | None
    currency: str | None
    statement_from: date | None
    statement_to: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    transactions: list[ParsedTransaction]

    # CSV format metadata
    detected_encoding: str
    detected_delimiter: str
    detected_format: str
    raw_headers: dict                   # {column_index: header_name}
    column_mapping: ColumnMapping

    # Quality metrics
    total_rows_parsed: int
    total_rows_skipped: int
    parser_version: str = "csv-parser-v1.0.0"

    # Aggregated warnings
    warnings: list[dict] = field(default_factory=list)


@dataclass
class ParsedInvoiceLineItem:
    line_number: int
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    sku: str | None = None
    unit_of_measure: str | None = None


@dataclass
class ParsedInvoice:
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    discount_amount: Decimal | None = None
    total_amount: Decimal | None = None
    raw_vendor_name: str | None = None
    raw_date_text: str | None = None
    raw_total_text: str | None = None
    confidence: Decimal = Decimal("1.000")
    notes: str | None = None
    line_items: list[ParsedInvoiceLineItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser_version: str = "pdf-parser-v1.0.0"
