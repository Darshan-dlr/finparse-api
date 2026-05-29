"""
PDF Invoice Parser — Heuristic Pipeline with Table Extraction

Architecture:
  Stage 1: Text & Table Extraction -> Uses pdfplumber to read pages
  Stage 2: Heuristic Field Mapping  -> Uses regex / text patterns for total, date, invoice number, vendor, currency
  Stage 3: Table Parsing & Mapping -> Identifies line items table and parses columns
  Stage 4: Post-Processing & Reconciliation -> Reconciles line totals with invoice total and flags warnings

Design principles:
  - Never crash on bad data; collect warnings and continue.
  - Return ParsedInvoice dataclass representing parsed contents.
  - SOTS recommendations for ML/AI document parsing added in comments/documentation.
"""
import io
import re
from datetime import date
from decimal import Decimal
import pdfplumber

from app.core.logging import get_logger
from app.utils.amount_parser import parse_amount
from app.utils.date_parser import parse_date
from app.parsers.base import BaseParser
from app.parsers.schemas import ParsedInvoiceLineItem, ParsedInvoice
from app.parsers.constants import (
    PDF_PARSER_VERSION as PARSER_VERSION,
    PDF_INV_NUM_PATTERNS as INV_NUM_PATTERNS,
    PDF_INV_DATE_PATTERNS as INV_DATE_PATTERNS,
    PDF_DUE_DATE_PATTERNS as DUE_DATE_PATTERNS,
    PDF_SUBTOTAL_PATTERNS as SUBTOTAL_PATTERNS,
    PDF_TAX_PATTERNS as TAX_PATTERNS,
    PDF_DISCOUNT_PATTERNS as DISCOUNT_PATTERNS,
    PDF_TOTAL_PATTERNS as TOTAL_PATTERNS,
    PDF_VENDOR_PATTERNS as VENDOR_PATTERNS,
    PDF_CURRENCY_MAP as CURRENCY_MAP,
    PDF_HEADER_MAPS as HEADER_MAPS,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PDF Parser
# ═══════════════════════════════════════════════════════════════════════════════

class PDFParser(BaseParser[ParsedInvoice]):
    """
    Parses PDF invoices using pdfplumber to extract text and tables.
    Applies heuristic rules to construct a ParsedInvoice.
    """

    def parse(self, content: bytes) -> ParsedInvoice:
        """
        Main parser entrypoint.
        """
        warnings = []
        text = ""
        tables = []

        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                    
                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(page_tables)
        except Exception as e:
            logger.exception("Failed to extract data using pdfplumber", error=str(e))
            warnings.append(f"pdfplumber extraction warning: {e}")

        # ── 1. Parse core fields ──────────────────────────────────────────────
        invoice_number = self._extract_invoice_number(text)
        invoice_date, raw_date_text = self._extract_date(text, INV_DATE_PATTERNS)
        due_date, _ = self._extract_date(text, DUE_DATE_PATTERNS)
        currency = self._extract_currency(text)
        vendor_name = self._extract_vendor_name(text)

        subtotal, _ = self._extract_amount(text, SUBTOTAL_PATTERNS)
        tax_amount, _ = self._extract_amount(text, TAX_PATTERNS)
        discount_amount, _ = self._extract_amount(text, DISCOUNT_PATTERNS)
        total_amount, raw_total_text = self._extract_amount(text, TOTAL_PATTERNS)

        # Calculate confidence score
        confidence = Decimal("1.00")
        unextracted_fields = []
        if not invoice_number:
            unextracted_fields.append("invoice_number")
        if not invoice_date:
            unextracted_fields.append("invoice_date")
        if not total_amount:
            unextracted_fields.append("total_amount")
        if not vendor_name:
            unextracted_fields.append("vendor_name")

        if unextracted_fields:
            # Deduct 0.15 for each key missing field
            deduction = Decimal("0.15") * len(unextracted_fields)
            confidence = max(Decimal("0.10"), Decimal("1.00") - deduction)
            warnings.append(f"Missing core fields: {', '.join(unextracted_fields)}")

        # ── 2. Parse Line Items Table ─────────────────────────────────────────
        line_items = self._parse_tables(tables, warnings)
        if not line_items:
            # Fallback text parsing for line items if table extraction yields nothing
            lines = text.split("\n")
            in_table = False
            line_num = 1
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # If we see the header line
                if re.search(r"\b(?:description|item|details|particulars)\b.*\b(?:qty|quantity|units)\b.*\b(?:price|rate|cost)\b.*\b(?:total|amount)\b", line, re.IGNORECASE):
                    in_table = True
                    continue
                
                # If we are in the table block
                if in_table:
                    # Check if line looks like a subtotal/total/summary line to exit table
                    if re.search(r"^\s*(subtotal|total|tax|gst|vat|balance|discount)\b", line, re.IGNORECASE):
                        in_table = False
                        break
                    
                    # Regex to match: Description Qty Price Total
                    m = re.match(r"^(.+?)\s+(\d+)\s+([£$€₹¥₩]?\s*[+-]?\s*[\d,.]+)\s+([£$€₹¥₩]?\s*[+-]?\s*[\d,.]+)$", line)
                    if m:
                        desc = m.group(1).strip()
                        qty_str = m.group(2).strip()
                        price_str = m.group(3).strip()
                        total_str = m.group(4).strip()
                        
                        qty = None
                        parsed_qty = parse_amount(qty_str)
                        if parsed_qty:
                            qty = parsed_qty.value
                            
                        price = None
                        parsed_price = parse_amount(price_str)
                        if parsed_price:
                            price = parsed_price.value
                            
                        total = None
                        parsed_total = parse_amount(total_str)
                        if parsed_total:
                            total = parsed_total.value
                            
                        item = ParsedInvoiceLineItem(
                            line_number=line_num,
                            description=desc,
                            quantity=qty,
                            unit_price=price,
                            line_total=total,
                        )
                        line_items.append(item)
                        line_num += 1

        # ── 3. Postprocessing & Reconciliation ────────────────────────────────
        self._reconcile(subtotal, tax_amount, discount_amount, total_amount, line_items, warnings)

        return ParsedInvoice(
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            currency=currency,
            subtotal=subtotal,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            total_amount=total_amount,
            raw_vendor_name=vendor_name,
            raw_date_text=raw_date_text,
            raw_total_text=raw_total_text,
            confidence=confidence,
            notes="Parsed via heuristics and tables extraction." if not warnings else f"Warnings: {'; '.join(warnings)}",
            line_items=line_items,
            warnings=warnings,
            parser_version=PARSER_VERSION,
        )

    # ── Field Extractors ──────────────────────────────────────────────────────

    def _extract_invoice_number(self, text: str) -> str | None:
        for pattern in INV_NUM_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
        return None

    def _extract_date(self, text: str, patterns: list[re.Pattern]) -> tuple[date | None, str | None]:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                # Split at multiple spaces or newlines to capture only local token
                candidate = re.split(r'\s{2,}|\n|\r', candidate)[0].strip()
                # Find date-like string
                date_match = re.search(r"(\d{1,4}[-./]\d{1,2}[-./]\d{1,4}|\d{1,2}\s+[A-Za-z]+\s+\d{2,4}|[A-Za-z]+\s+\d{1,2},\s*\d{2,4})", candidate)
                if date_match:
                    clean_str = date_match.group(1).strip()
                    try:
                        parsed = parse_date(clean_str)
                        if parsed:
                            return parsed.value, clean_str
                    except Exception:
                        pass
        return None, None

    def _extract_amount(self, text: str, patterns: list[re.Pattern]) -> tuple[Decimal | None, str | None]:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                candidate = re.split(r'\s{2,}|\n|\r', candidate)[0].strip()
                # Strip leading parentheticals like (10%) or (exempt)
                candidate = re.sub(r"^\([^)]*\)\s*[:\-]?\s*", "", candidate).strip()
                # Regex matching digits with decimals, currency prefixes, signs
                amt_match = re.search(r"([+-]?\s*[\d,.\s]+(?:\s*[DdRrCcRr]+)?|[£$€₹¥₩]*\s*[+-]?\s*[\d,.]+)", candidate)
                if amt_match:
                    clean_str = amt_match.group(1).strip()
                    try:
                        parsed = parse_amount(clean_str)
                        if parsed:
                            return parsed.value, clean_str
                    except Exception:
                        pass
        return None, None

    def _extract_currency(self, text: str) -> str | None:
        # Check standard ISO codes or symbols
        occurrences = {}
        for token, iso in CURRENCY_MAP.items():
            count = len(re.findall(re.escape(token), text, re.IGNORECASE))
            if count > 0:
                occurrences[iso] = occurrences.get(iso, 0) + count
        
        if occurrences:
            # Return most frequent currency code
            return max(occurrences, key=occurrences.get)
        return "USD"  # Default fallback

    def _extract_vendor_name(self, text: str) -> str | None:
        for pattern in VENDOR_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                candidate = re.split(r'\s{2,}|\n|\r', candidate)[0].strip()
                if len(candidate) > 2 and len(candidate) < 100:
                    return candidate

        # Fallback to the first non-empty text line that doesn't contain labels or numbers
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        for line in lines:
            if (
                len(line) > 3 
                and len(line) < 100 
                and not re.search(r"(invoice|statement|due|date|amount|total|#|\d{5,})", line, re.IGNORECASE)
            ):
                return line
        return None

    # ── Table Parsing ─────────────────────────────────────────────────────────

    def _parse_tables(self, tables: list[list[list[str]]], warnings: list[str]) -> list[ParsedInvoiceLineItem]:
        """
        Identify the correct table and parse its columns.
        """
        line_items = []
        best_table = None
        best_mapping = None
        best_header_row_index = -1
        max_headers_matched = 1

        for table in tables:
            if not table or len(table) < 2:
                continue

            # Look for a header row in the first few rows of the table
            for row_idx in range(min(5, len(table))):
                row = [str(cell or "").strip().lower() for cell in table[row_idx]]
                
                # Check how many headers we can map
                mapping = {}
                matches = 0
                for col_idx, cell_text in enumerate(row):
                    for logical_field, keywords in HEADER_MAPS.items():
                        if any(keyword in cell_text for keyword in keywords):
                            mapping[logical_field] = col_idx
                            matches += 1
                            break

                # We need at least description and either unit_price or line_total
                if matches > max_headers_matched and "description" in mapping:
                    max_headers_matched = matches
                    best_table = table
                    best_mapping = mapping
                    best_header_row_index = row_idx

        if not best_table:
            logger.info("No structured line items table identified in PDF.")
            return []

        # Parse subsequent rows
        line_num = 1
        for row_idx in range(best_header_row_index + 1, len(best_table)):
            row = best_table[row_idx]
            if not row or all(cell is None or str(cell).strip() == "" for cell in row):
                continue  # skip empty lines

            desc_idx = best_mapping.get("description")
            qty_idx = best_mapping.get("quantity")
            price_idx = best_mapping.get("unit_price")
            total_idx = best_mapping.get("line_total")
            sku_idx = best_mapping.get("sku")
            uom_idx = best_mapping.get("unit_of_measure")
            tax_rate_idx = best_mapping.get("tax_rate")
            tax_amount_idx = best_mapping.get("tax_amount")

            description = str(row[desc_idx]).strip() if desc_idx is not None and row[desc_idx] else None
            
            # If description looks like metadata or summary totals, skip it
            if not description or re.search(r"^\s*(subtotal|total|tax|gst|vat|balance|discount)\b", description, re.IGNORECASE):
                continue

            quantity = None
            if qty_idx is not None and row[qty_idx]:
                parsed_qty = parse_amount(str(row[qty_idx]))
                if parsed_qty:
                    quantity = parsed_qty.value

            unit_price = None
            if price_idx is not None and row[price_idx]:
                parsed_price = parse_amount(str(row[price_idx]))
                if parsed_price:
                    unit_price = parsed_price.value

            line_total = None
            if total_idx is not None and row[total_idx]:
                parsed_total = parse_amount(str(row[total_idx]))
                if parsed_total:
                    line_total = parsed_total.value

            # Fallbacks / Inferences
            if line_total is None and quantity is not None and unit_price is not None:
                line_total = quantity * unit_price
            elif unit_price is None and line_total is not None and quantity is not None and quantity > 0:
                unit_price = line_total / quantity

            sku = str(row[sku_idx]).strip() if sku_idx is not None and row[sku_idx] else None
            uom = str(row[uom_idx]).strip() if uom_idx is not None and row[uom_idx] else None

            tax_rate = None
            if tax_rate_idx is not None and row[tax_rate_idx]:
                parsed_rate = parse_amount(str(row[tax_rate_idx]))
                if parsed_rate:
                    tax_rate = parsed_rate.value

            tax_amount = None
            if tax_amount_idx is not None and row[tax_amount_idx]:
                parsed_tax_amt = parse_amount(str(row[tax_amount_idx]))
                if parsed_tax_amt:
                    tax_amount = parsed_tax_amt.value

            item = ParsedInvoiceLineItem(
                line_number=line_num,
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                line_total=line_total,
                sku=sku,
                unit_of_measure=uom,
                tax_rate=tax_rate,
                tax_amount=tax_amount,
            )
            line_items.append(item)
            line_num += 1

        return line_items

    # ── Reconciliation ────────────────────────────────────────────────────────

    def _reconcile(
        self,
        subtotal: Decimal | None,
        tax_amount: Decimal | None,
        discount_amount: Decimal | None,
        total_amount: Decimal | None,
        line_items: list[ParsedInvoiceLineItem],
        warnings: list[str],
    ) -> None:
        """
        Verifies mathematical consistency of totals vs line items.
        """
        if not line_items:
            return

        lines_sum = sum(item.line_total for item in line_items if item.line_total is not None)
        
        # 1. Line items sum vs Subtotal
        if subtotal is not None:
            diff = abs(lines_sum - subtotal)
            if diff > Decimal("0.05"):
                warnings.append(f"Reconciliation note: Sum of line items ({lines_sum}) differs from Subtotal ({subtotal}) by {diff}")
        
        # 2. Reconstruct Total from Subtotal / Lines + Tax - Discount
        calc_total = subtotal if subtotal is not None else lines_sum
        if tax_amount is not None:
            calc_total += tax_amount
        if discount_amount is not None:
            calc_total -= discount_amount

        if total_amount is not None:
            diff = abs(calc_total - total_amount)
            if diff > Decimal("0.05"):
                warnings.append(f"Reconciliation note: Calculated total ({calc_total}) differs from Total ({total_amount}) by {diff}")

# ═══════════════════════════════════════════════════════════════════════════════
# Production ML/AI/LLM Parsing Recommendation
# ═══════════════════════════════════════════════════════════════════════════════
# > [!TIP]
# > Heuristics and table extractions are fast, local, and cost-effective, but can fail
# > on highly customized or irregular layouts.
# > For production systems:
# > 1. **LLMs (Gemini Flash/Pro, GPT-4o)**: Pass document screenshots or extracted text
# >    directly into a multi-modal LLM with structured output schemas (JSON Mode / Structured Outputs).
# > 2. **LayoutLM / Donut (Transformer-based models)**: Fine-tune visual document models
# >    capable of handling spatial layouts (bounding boxes) + text to extract tabular items.
# > 3. **Document AI Services (Google Cloud Document AI, AWS Textract)**: Standardized
# >    pre-trained document parsing models designed specifically for invoices.
