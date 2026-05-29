"""
Unit tests for the PDF Parser — verifies text heuristics, table parsing, and reconciliation.
Run with: conda run -n finparse pytest tests/test_pdf_parser.py -v
"""
import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock, patch

from app.parsers.pdf_parser import PDFParser, ParsedInvoice


class TestPDFParser:

    def test_pdf_parser_heuristics_and_tables(self):
        """Test complete extraction of invoice metadata and line items from a mocked PDF."""
        mock_text = """
        ACME Corp
        From: ACME Corp
        Invoice No: INV-98765
        Date: 2026-05-29
        Due Date: 2026-06-29
        Subtotal: 1500.00
        Tax: 120.00
        Total: 1620.00
        Currency: USD
        """
        mock_table = [
            ["Description", "Qty", "Unit Price", "Total"],
            ["Consulting Services", "10", "100.00", "1000.00"],
            ["Software Licenses", "5", "100.00", "500.00"],
        ]

        mock_page = MagicMock()
        mock_page.extract_text.return_value = mock_text
        mock_page.extract_tables.return_value = [mock_table]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open:
            mock_open.return_value.__enter__.return_value = mock_pdf

            parser = PDFParser()
            result: ParsedInvoice = parser.parse(b"dummy pdf bytes")

            # Core assertions
            assert result.invoice_number == "INV-98765"
            assert result.invoice_date == date(2026, 5, 29)
            assert result.due_date == date(2026, 6, 29)
            assert result.currency == "USD"
            assert result.raw_vendor_name == "ACME Corp"
            assert result.subtotal == Decimal("1500.00")
            assert result.tax_amount == Decimal("120.00")
            assert result.total_amount == Decimal("1620.00")
            assert result.confidence == Decimal("1.000")
            assert not result.warnings

            # Line items assertions
            assert len(result.line_items) == 2
            assert result.line_items[0].line_number == 1
            assert result.line_items[0].description == "Consulting Services"
            assert result.line_items[0].quantity == Decimal("10")
            assert result.line_items[0].unit_price == Decimal("100.00")
            assert result.line_items[0].line_total == Decimal("1000.00")

            assert result.line_items[1].line_number == 2
            assert result.line_items[1].description == "Software Licenses"
            assert result.line_items[1].quantity == Decimal("5")
            assert result.line_items[1].unit_price == Decimal("100.00")
            assert result.line_items[1].line_total == Decimal("500.00")

    def test_pdf_parser_reconciliation_warning(self):
        """Test that a reconciliation note warning is added when sums mismatch."""
        mock_text = """
        Vendor: TechLabs LLC
        Invoice No: INV-4455
        Date: 2026-05-29
        Subtotal: 1000.00
        Tax: 80.00
        Total: 1080.00
        """
        # Sum of items (150.00) does not match subtotal (1000.00)
        mock_table = [
            ["Description", "Qty", "Price", "Total"],
            ["Broken Cable", "1", "150.00", "150.00"],
        ]

        mock_page = MagicMock()
        mock_page.extract_text.return_value = mock_text
        mock_page.extract_tables.return_value = [mock_table]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open:
            mock_open.return_value.__enter__.return_value = mock_pdf

            parser = PDFParser()
            result: ParsedInvoice = parser.parse(b"dummy pdf bytes")

            assert len(result.warnings) > 0
            assert any("differs from Subtotal" in w for w in result.warnings)

    def test_pdf_parser_no_table(self):
        """Verify fallback behavior when PDF contains no structured line items table."""
        mock_text = """
        Global Solutions Inc
        Invoice No: INV-9900
        Date: May 29, 2026
        Total: 500.00
        """

        mock_page = MagicMock()
        mock_page.extract_text.return_value = mock_text
        mock_page.extract_tables.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open:
            mock_open.return_value.__enter__.return_value = mock_pdf

            parser = PDFParser()
            result: ParsedInvoice = parser.parse(b"dummy pdf bytes")

            assert result.invoice_number == "INV-9900"
            assert result.invoice_date == date(2026, 5, 29)
            assert result.total_amount == Decimal("500.00")
            assert result.raw_vendor_name == "Global Solutions Inc"
            assert len(result.line_items) == 0

    def test_pdf_parser_fallback_vendor(self):
        """Verify that vendor name falls back to first text line when 'From:' or 'Vendor:' is missing."""
        mock_text = """
        HackerNews Publishing Co.
        Invoice # 8812
        Date: 2026-05-29
        Total: $45.00
        """

        mock_page = MagicMock()
        mock_page.extract_text.return_value = mock_text
        mock_page.extract_tables.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open:
            mock_open.return_value.__enter__.return_value = mock_pdf

            parser = PDFParser()
            result = parser.parse(b"dummy pdf bytes")
            assert result.raw_vendor_name == "HackerNews Publishing Co."
            assert result.invoice_number == "8812"
            assert result.total_amount == Decimal("45.00")
            assert result.currency == "USD"

    def test_pdf_parser_real_invoice(self):
        """Test parsing a real, non-mocked PDF invoice generated by ReportLab."""
        from pathlib import Path
        pdf_path = Path(__file__).parent / "sample_files" / "sample_invoice.pdf"
        
        # Ensure the file exists
        assert pdf_path.exists()
        
        content = pdf_path.read_bytes()
        parser = PDFParser()
        result = parser.parse(content)

        assert result.invoice_number == "INV-2026-001"
        assert result.invoice_date == date(2026, 5, 29)
        assert result.due_date == date(2026, 6, 29)
        assert result.currency == "USD"
        assert result.raw_vendor_name == "ACME Global Solutions"
        assert result.subtotal == Decimal("1500.00")
        assert result.tax_amount == Decimal("150.00")
        assert result.total_amount == Decimal("1650.00")
        
        assert len(result.line_items) == 2
        assert result.line_items[0].description == "Cloud Hosting Services"
        assert result.line_items[0].quantity == Decimal("1")
        assert result.line_items[0].unit_price == Decimal("1000.00")
        assert result.line_items[0].line_total == Decimal("1000.00")

        assert result.line_items[1].description == "Database Administration"
        assert result.line_items[1].quantity == Decimal("5")
        assert result.line_items[1].unit_price == Decimal("100.00")
        assert result.line_items[1].line_total == Decimal("500.00")
