"""
Pydantic schemas for Vendors, Invoices, and InvoiceLineItems.
"""
from datetime import date, datetime
from decimal import Decimal
import uuid

from app.schemas.common import BaseSchema


class VendorRead(BaseSchema):
    id: uuid.UUID
    canonical_name: str
    raw_names: list[str]
    country: str | None
    tax_id: str | None
    created_at: datetime
    updated_at: datetime


class InvoiceLineItemRead(BaseSchema):
    id: uuid.UUID
    invoice_id: uuid.UUID
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    tax_rate: Decimal | None
    tax_amount: Decimal | None
    sku: str | None
    unit_of_measure: str | None
    created_at: datetime


class InvoiceRead(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    processing_job_id: uuid.UUID
    vendor_id: uuid.UUID | None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal: Decimal | None
    tax_amount: Decimal | None
    discount_amount: Decimal | None
    total_amount: Decimal | None
    raw_vendor_name: str | None
    raw_date_text: str | None
    raw_total_text: str | None
    confidence: Decimal | None
    notes: str | None
    page_range_start: int | None
    page_range_end: int | None
    invoice_index: int
    extracted_at: datetime
    updated_at: datetime


class InvoiceWithLineItemsRead(InvoiceRead):
    vendor: VendorRead | None
    line_items: list[InvoiceLineItemRead]
