"""
Integration tests for FastAPI endpoints.
"""
from decimal import Decimal
from datetime import date
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.bank_statement import BankStatement, BankTransaction
from app.models.document import Document
from app.models.invoice import Invoice, InvoiceLineItem, Vendor
from app.parsers.pdf_parser import ParsedInvoice, ParsedInvoiceLineItem
from app.validators.file_validator import ValidatedFile

import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient):
    """Test health check route."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_upload_and_retrieve_bank_statement(
    async_client: AsyncClient, db_session: AsyncSession, standard_csv_bytes: bytes
):
    """Test full upload, parsing, job retrieval, and document get flow."""
    # ── Upload ────────────────────────────────────────────────────────────────
    files = {"file": ("standard_statement.csv", standard_csv_bytes, "text/csv")}
    response = await async_client.post("/api/v1/documents/upload", files=files)
    assert response.status_code == 202
    
    data = response.json()
    assert "document_id" in data
    assert "job_id" in data
    assert data["status"] in ("completed", "partial")  # CSV parser runs synchronously in our current service implementation

    doc_id = data["document_id"]
    job_id = data["job_id"]

    # ── Verify DB Persistence ────────────────────────────────────────────────
    # Check that bank statement metadata was successfully written
    stmt_result = await db_session.execute(
        select(BankStatement).where(BankStatement.document_id == uuid.UUID(doc_id))
    )
    statement = stmt_result.scalar_one_or_none()
    assert statement is not None
    assert statement.detected_delimiter == ","
    
    # Check that bank transactions were successfully written
    tx_result = await db_session.execute(
        select(BankTransaction).where(BankTransaction.bank_statement_id == statement.id)
    )
    transactions = tx_result.scalars().all()
    assert len(transactions) > 0
    # Every transaction should have amount >= 0 and a direction
    for tx in transactions:
        assert tx.amount >= 0
        assert tx.direction in ("C", "D")

    # ── Get Document ──────────────────────────────────────────────────────────
    doc_response = await async_client.get(f"/api/v1/documents/{doc_id}")
    assert doc_response.status_code == 200
    doc_data = doc_response.json()
    assert doc_data["id"] == doc_id
    assert doc_data["original_name"] == "standard_statement.csv"
    assert doc_data["document_type"] == "bank_statement"
    assert doc_data["file_type"] == "csv"

    # ── Get Job Status ────────────────────────────────────────────────────────
    job_response = await async_client.get(f"/api/v1/documents/{doc_id}/job")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["id"] == job_id
    assert job_data["document_id"] == doc_id
    assert job_data["status"] in ("completed", "partial")

    # ── List Documents ────────────────────────────────────────────────────────
    list_response = await async_client.get("/api/v1/documents/")
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["count"] == 1
    assert list_data["items"][0]["id"] == doc_id

    # ── Duplicate Rejection (allow_reprocess = False) ─────────────────────────
    # Re-uploading exact same file bytes should raise 409
    dup_response = await async_client.post("/api/v1/documents/upload", files=files)
    assert dup_response.status_code == 409
    assert dup_response.json()["error"] == "DUPLICATE_FILE"

    # ── Duplicate Reprocessing (allow_reprocess = True) ────────────────────────
    reproc_response = await async_client.post(
        f"/api/v1/documents/upload?allow_reprocess=true", files=files
    )
    assert reproc_response.status_code == 202
    reproc_data = reproc_response.json()
    assert reproc_data["document_id"] == doc_id
    assert reproc_data["job_id"] != job_id  # New job ID created
    assert reproc_data["is_reprocess"] is True

    # ── Delete Document (Soft Delete) ─────────────────────────────────────────
    delete_response = await async_client.delete(f"/api/v1/documents/{doc_id}")
    assert delete_response.status_code == 204

    # Verify DB soft-delete flag
    doc_result = await db_session.execute(
        select(Document).where(Document.id == uuid.UUID(doc_id))
    )
    doc_in_db = doc_result.scalar_one_or_none()
    assert doc_in_db is not None
    assert doc_in_db.is_deleted is True
    assert doc_in_db.deleted_at is not None

    # Get after delete should return 404
    get_after_delete = await async_client.get(f"/api/v1/documents/{doc_id}")
    assert get_after_delete.status_code == 404


@pytest.mark.asyncio
async def test_upload_and_retrieve_pdf_invoice(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Test full upload and parsing pipeline for a PDF invoice."""
    # 100-byte valid PDF prefix bytes to pass FileValidator check
    pdf_bytes = b"%PDF-1.5\n" + b"x" * 90

    # Define mock return value
    mock_parsed_invoice = ParsedInvoice(
        invoice_number="INV-9988",
        invoice_date=date(2026, 5, 29),
        due_date=date(2026, 6, 29),
        currency="USD",
        subtotal=Decimal("2000.00"),
        tax_amount=Decimal("160.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("2160.00"),
        raw_vendor_name="Global Tech Solutions",
        confidence=Decimal("1.000"),
        line_items=[
            ParsedInvoiceLineItem(
                line_number=1,
                description="Custom Software Development",
                quantity=Decimal("20"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("2000.00"),
            )
        ],
        warnings=[]
    )

    # ── Upload ────────────────────────────────────────────────────────────────
    files = {"file": ("invoice_test.pdf", pdf_bytes, "application/pdf")}
    
    validated_mock = ValidatedFile(
        content=pdf_bytes,
        checksum_sha256="af3588dfb22c",
        detected_mime="application/pdf",
        safe_filename="invoice_test.pdf",
        original_filename="invoice_test.pdf",
        file_size_bytes=len(pdf_bytes),
        file_extension=".pdf",
        pdf_page_count=1,
        pdf_is_encrypted=False,
        ocr_needed=False,
    )

    from unittest.mock import AsyncMock
    with patch("app.services.document_service.FileValidator.validate", new_callable=AsyncMock) as mock_validate:
        mock_validate.return_value = validated_mock
        with patch("app.services.document_service.PDFParser.parse", return_value=mock_parsed_invoice):
            response = await async_client.post("/api/v1/documents/upload", files=files)
            assert response.status_code == 202
        
        data = response.json()
        assert "document_id" in data
        assert "job_id" in data
        assert data["status"] in ("completed", "partial")

        doc_id = data["document_id"]
        job_id = data["job_id"]

        # ── Verify DB Persistence ──────────────────────────────────────────────
        # Check vendor
        vendor_result = await db_session.execute(
            select(Vendor).where(Vendor.canonical_name == "Global Tech Solutions")
        )
        vendor = vendor_result.scalar_one_or_none()
        assert vendor is not None

        # Check invoice
        invoice_result = await db_session.execute(
            select(Invoice).where(Invoice.document_id == uuid.UUID(doc_id))
        )
        invoice = invoice_result.scalar_one_or_none()
        assert invoice is not None
        assert invoice.invoice_number == "INV-9988"
        assert invoice.total_amount == Decimal("2160.00")
        assert invoice.vendor_id == vendor.id

        # Check line items
        lines_result = await db_session.execute(
            select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice.id)
        )
        line_items = lines_result.scalars().all()
        assert len(line_items) == 1
        assert line_items[0].description == "Custom Software Development"
        assert line_items[0].line_total == Decimal("2000.00")

        # ── Get Document ──────────────────────────────────────────────────────
        doc_response = await async_client.get(f"/api/v1/documents/{doc_id}")
        assert doc_response.status_code == 200
        doc_data = doc_response.json()
        assert doc_data["id"] == doc_id
        assert doc_data["document_type"] == "invoice"
        assert doc_data["file_type"] == "pdf"

        # ── Get Job Status ────────────────────────────────────────────────────
        job_response = await async_client.get(f"/api/v1/documents/{doc_id}/job")
        assert job_response.status_code == 200
        job_data = job_response.json()
        assert job_data["status"] == "completed"
