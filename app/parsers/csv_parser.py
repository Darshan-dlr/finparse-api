"""
CSV Bank Statement Parser — Full Pipeline

Architecture: 5-stage pipeline, each stage is independently testable.

  Stage 1: FileReader      → decode bytes, detect encoding
  Stage 2: FormatDetector  → detect delimiter, find header row, map columns
  Stage 3: RowFilter       → skip blanks, metadata rows, summary rows
  Stage 4: RowParser       → parse each row (date, amount, description, type)
  Stage 5: PostProcessor   → validate balance, compute stats, build result

Design principles:
  - Never crash on bad data; collect warnings and continue
  - Always preserve raw values for debugging
  - Amount is always positive; direction is explicit ('C' / 'D')
  - All warnings attached to the ParsedBankStatement result
"""
import csv
import io
import re
from decimal import Decimal

import chardet

from app.core.exceptions import (
    CSVEncodingError,
    CSVMissingRequiredColumnsError,
    CSVNoDataRowsError,
    CSVParseError,
)
from app.core.logging import get_logger
from app.utils.amount_parser import ParsedAmount, parse_amount, parse_split_amounts
from app.utils.date_parser import ParsedDate, infer_date_format_hint, parse_date
from app.models.enums import TransactionType
from app.parsers.base import BaseParser
from app.parsers.schemas import ParsedTransaction, ColumnMapping, ParsedBankStatement
from app.parsers.constants import (
    CSV_PARSER_VERSION as PARSER_VERSION,
    CSV_MIN_DATA_ROWS as MIN_DATA_ROWS,
    CSV_SKIP_ROW_PATTERNS as SKIP_ROW_PATTERNS,
    CSV_TYPE_KEYWORD_MAP as TYPE_KEYWORD_MAP,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1: FileReader — decode bytes, detect encoding
# ═══════════════════════════════════════════════════════════════════════════════

class FileReader:
    """
    Reads raw bytes → decoded text string.
    Handles: UTF-8, UTF-8-BOM, Latin-1, Windows-1252, UTF-16.
    """

    SUPPORTED_ENCODINGS = [
        "utf-8-sig",    # UTF-8 with BOM (Excel exports) — try first
        "utf-8",
        "latin-1",
        "windows-1252",
        "utf-16",
    ]

    def read(self, content: bytes) -> tuple[str, str]:
        """
        Decode bytes to string.

        Returns:
            (decoded_text, detected_encoding)

        Raises:
            CSVEncodingError: If all known encodings fail.
        """
        # ── Use chardet for detection ────────────────────────────────────────
        detected = chardet.detect(content)
        chardet_encoding = detected.get("encoding") or "utf-8"
        chardet_confidence = detected.get("confidence", 0)

        logger.debug(
            "Encoding detection",
            chardet_encoding=chardet_encoding,
            confidence=chardet_confidence,
        )

        # ── Build candidate list (chardet first, then fallbacks) ─────────────
        candidates = [chardet_encoding] + [
            e for e in self.SUPPORTED_ENCODINGS if e.lower() != chardet_encoding.lower()
        ]

        last_error: Exception | None = None
        for encoding in candidates:
            try:
                text = content.decode(encoding)
                # Check for replacement characters (sign of wrong encoding)
                replacement_count = text.count("\ufffd")
                if replacement_count > 10:
                    logger.warning(
                        "High replacement char count — encoding may be wrong",
                        encoding=encoding,
                        replacement_count=replacement_count,
                    )
                    continue
                logger.info("Decoded CSV", encoding=encoding)
                return text, encoding
            except (UnicodeDecodeError, LookupError) as e:
                last_error = e
                continue

        # ── Last resort: decode with errors='replace' ────────────────────────
        logger.warning("All encodings failed; falling back to UTF-8 with replacement chars")
        try:
            text = content.decode("utf-8", errors="replace")
            return text, "utf-8 (forced, may contain errors)"
        except Exception:
            raise CSVEncodingError(
                f"Cannot decode file content. Tried: {candidates}. Last error: {last_error}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2: FormatDetector — delimiter, header row, column mapping
# ═══════════════════════════════════════════════════════════════════════════════

# Column name synonym maps — order matters (more specific first)
# Covers: Standard, HDFC, SBI, ICICI, European/German, and common variants
DATE_COLUMNS = [
    # Standard / generic
    "transaction date", "txn date", "trans date", "date",
    "posting date", "post date", "entry date", "booking date",
    # German / European
    "datum", "buchungsdatum", "buchungstag", "wertstellungsdatum",
    # HDFC / Indian bank specific
    "value dt", "tran date", "val date",
]
VALUE_DATE_COLUMNS = [
    "value date", "settlement date", "effective date",
    "value dt", "wertstellung", "val date",
]
DESCRIPTION_COLUMNS = [
    "transaction details", "transaction description", "narration",
    "description", "particulars", "details", "remarks", "memo",
    "reference details", "trans description",
    # German
    "buchungstext", "verwendungszweck", "betreff",
    # HDFC / SBI
    "tran particular", "transaction particulars",
]
REFERENCE_COLUMNS = [
    "reference number", "ref number", "ref no", "reference",
    "cheque no", "cheque number", "check no", "transaction id",
    "txn id", "trans id", "utr", "utr number",
    # HDFC: Chq./Ref.No.
    "chq./ref.no.", "chq/ref no", "chq.ref.no", "cheque ref no",
]
AMOUNT_COLUMNS = [
    "transaction amount", "txn amount", "trans amount",
    "amount (inr)", "amount (usd)", "amount",
    # German
    "betrag", "umsatz",
]
DEBIT_COLUMNS = [
    "debit amount", "withdrawal amount", "withdrawal (dr)",
    "debit", "dr", "withdrawal", "dr amount", "debit(dr)",
    # HDFC format
    "withdrawal amt (dr)", "debit amt", "dr amt",
    # SBI / other Indian banks
    "withdrawals", "debit (dr)",
]
CREDIT_COLUMNS = [
    "credit amount", "deposit amount", "deposit (cr)",
    "credit", "cr", "deposit", "cr amount", "credit(cr)",
    # HDFC format
    "deposit amt (cr)", "credit amt", "cr amt",
    # SBI / other
    "deposits", "credit (cr)",
]
BALANCE_COLUMNS = [
    "running balance", "available balance", "closing balance",
    "balance", "bal", "closing bal",
    # German
    "kontostand", "saldo", "endbestand",
    # HDFC
    "closing balance",
]
CURRENCY_COLUMNS = [
    "currency", "ccy", "curr",
    # German
    "währung", "wahrung",
]
TYPE_COLUMNS = ["transaction type", "txn type", "type", "trans type"]


class FormatDetector:
    """
    Detects CSV format and maps column headers to logical fields.
    """

    CANDIDATE_DELIMITERS = [",", ";", "\t", "|", ":"]

    def detect(self, text: str) -> tuple[str, list[list[str]]]:
        """
        Detect delimiter and parse all rows.

        Returns:
            (detected_delimiter, all_rows_as_lists)
        """
        # ── Try csv.Sniffer first ────────────────────────────────────────────
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
            rows = self._parse_with_delimiter(text, delimiter)
            if self._looks_valid(rows):
                logger.info("Delimiter detected via Sniffer", delimiter=repr(delimiter))
                return delimiter, rows
        except csv.Error:
            pass

        # ── Manual detection: try each delimiter ─────────────────────────────
        best_delimiter = ","
        best_score = -1

        for delim in self.CANDIDATE_DELIMITERS:
            rows = self._parse_with_delimiter(text, delim)
            score = self._delimiter_score(rows)
            if score > best_score:
                best_score = score
                best_delimiter = delim

        rows = self._parse_with_delimiter(text, best_delimiter)
        logger.info("Delimiter detected via scoring", delimiter=repr(best_delimiter), score=best_score)
        return best_delimiter, rows

    def find_header_row(self, rows: list[list[str]]) -> int:
        """
        Find which row index contains the column headers.
        Some bank exports have 3-5 metadata rows before the actual table.

        Returns the 0-based index of the header row.
        """
        # Heuristic: header row contains known date/amount/description column names.
        # Use all known synonyms so German/HDFC headers are detected.
        header_signals = set(
            DATE_COLUMNS + AMOUNT_COLUMNS + DEBIT_COLUMNS
            + CREDIT_COLUMNS + DESCRIPTION_COLUMNS + BALANCE_COLUMNS
            + REFERENCE_COLUMNS + CURRENCY_COLUMNS + VALUE_DATE_COLUMNS
        )

        for i, row in enumerate(rows[:15]):  # Check first 15 rows max
            normalized_cells = [cell.strip().lower() for cell in row]
            # Check exact matches AND substring containment
            matches = 0
            for cell in normalized_cells:
                if cell in header_signals:
                    matches += 1
                elif any(sig in cell for sig in header_signals if len(sig) > 4):
                    # Substring: e.g. cell="withdrawal amt (dr)" contains "withdrawal"
                    matches += 1
            if matches >= 2:
                logger.info("Header row found", row_index=i, matches=matches)
                return i

        logger.warning("No header row detected; assuming row 0 is header")
        return 0

    def map_columns(self, header_row: list[str]) -> ColumnMapping:
        """
        Map header cell values to logical field indices.
        Returns a ColumnMapping with index positions.
        """
        mapping = ColumnMapping()
        normalized = [h.strip().lower() for h in header_row]

        def find_col(candidates: list[str]) -> int | None:
            # Pass 1: exact match
            for candidate in candidates:
                for i, h in enumerate(normalized):
                    if candidate == h:
                        return i
            # Pass 2: header starts with candidate
            for candidate in candidates:
                for i, h in enumerate(normalized):
                    if h.startswith(candidate) and len(candidate) > 3:
                        return i
            # Pass 3: candidate appears anywhere in header (for long compound headers)
            for candidate in candidates:
                for i, h in enumerate(normalized):
                    if candidate in h and len(candidate) > 4:
                        return i
            return None

        mapping.date = find_col(DATE_COLUMNS)
        mapping.value_date = find_col(VALUE_DATE_COLUMNS)
        mapping.description = find_col(DESCRIPTION_COLUMNS)
        mapping.reference = find_col(REFERENCE_COLUMNS)
        mapping.amount = find_col(AMOUNT_COLUMNS)
        mapping.debit = find_col(DEBIT_COLUMNS)
        mapping.credit = find_col(CREDIT_COLUMNS)
        mapping.balance = find_col(BALANCE_COLUMNS)
        mapping.currency = find_col(CURRENCY_COLUMNS)
        mapping.transaction_type = find_col(TYPE_COLUMNS)

        # Avoid date/value_date mapping to same column
        if mapping.date is not None and mapping.value_date == mapping.date:
            mapping.value_date = None

        logger.debug("Column mapping", mapping=mapping.__dict__)
        return mapping

    def validate_mapping(self, mapping: ColumnMapping, headers: list[str]) -> None:
        """Raise if mandatory columns are missing."""
        missing = []
        if mapping.date is None:
            missing.append("date")
        has_amount = (
            mapping.amount is not None
            or (mapping.debit is not None or mapping.credit is not None)
        )
        if not has_amount:
            missing.append("amount (or debit/credit)")

        if missing:
            raise CSVMissingRequiredColumnsError(
                missing=missing,
                available=[h.strip() for h in headers],
            )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _parse_with_delimiter(self, text: str, delimiter: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        return [row for row in reader]

    def _looks_valid(self, rows: list[list[str]]) -> bool:
        """Check if parsed rows have consistent column counts."""
        if not rows:
            return False
        col_counts = [len(r) for r in rows[:20] if r]
        if not col_counts:
            return False
        most_common = max(set(col_counts), key=col_counts.count)
        consistency = col_counts.count(most_common) / len(col_counts)
        return consistency >= 0.7 and most_common >= 2

    def _delimiter_score(self, rows: list[list[str]]) -> float:
        """Score a delimiter by consistency of column counts."""
        if not rows:
            return 0
        col_counts = [len(r) for r in rows[:20] if r]
        if not col_counts or max(col_counts) < 2:
            return 0
        most_common = max(set(col_counts), key=col_counts.count)
        consistency = col_counts.count(most_common) / len(col_counts)
        return consistency * most_common  # More columns + more consistent = higher score


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3: RowFilter — skip blanks, metadata, and summary rows
# ═══════════════════════════════════════════════════════════════════════════════

class RowFilter:
    """Filters out non-data rows before parsing."""

    def __init__(self, expected_col_count: int):
        self.expected_col_count = expected_col_count
        self.skipped: list[dict] = []

    def should_skip(self, row: list[str], row_index: int) -> bool:
        """
        Returns True if this row should be skipped.
        Appends to self.skipped for audit purposes.
        """
        reason = self._get_skip_reason(row, row_index)
        if reason:
            self.skipped.append({"row_index": row_index, "reason": reason, "content": row[:3]})
            return True
        return False

    def _get_skip_reason(self, row: list[str], row_index: int) -> str | None:
        # ── Blank row ───────────────────────────────────────────────────────
        if not row or all(cell.strip() == "" for cell in row):
            return "blank_row"

        # ── Too few columns (malformed) ──────────────────────────────────────
        if len(row) < max(2, self.expected_col_count - 2):
            return f"too_few_columns ({len(row)} vs expected ~{self.expected_col_count})"

        # ── Summary / metadata row ───────────────────────────────────────────
        first_cell = row[0].strip()
        if SKIP_ROW_PATTERNS.match(first_cell):
            return f"summary_row ({first_cell!r})"

        # ── Repeated header row (continuation pages) ─────────────────────────
        date_signals = {"date", "txn date", "transaction date", "value date"}
        if first_cell.strip().lower() in date_signals:
            return "repeated_header_row"

        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 4: RowParser — parse individual rows
# ═══════════════════════════════════════════════════════════════════════════════

class RowParser:
    """Parses a single CSV data row into a ParsedTransaction."""

    def __init__(self, mapping: ColumnMapping, dayfirst: bool = True):
        self.mapping = mapping
        self.dayfirst = dayfirst

    def parse(self, row: list[str], row_index: int) -> ParsedTransaction | None:
        """
        Parse one CSV row.

        Returns:
            ParsedTransaction on success.
            None if the row cannot be parsed at all (e.g., date missing).
        """
        warnings: list[str] = []
        m = self.mapping

        def get(col_index: int | None) -> str:
            if col_index is None or col_index >= len(row):
                return ""
            return row[col_index].strip()

        # ── Date ─────────────────────────────────────────────────────────────
        raw_date = get(m.date)
        try:
            parsed_date: ParsedDate | None = parse_date(raw_date, dayfirst=self.dayfirst)
        except ValueError as e:
            logger.warning("Date parse failure", row_index=row_index, raw=raw_date, error=str(e))
            return None  # Cannot continue without a date

        if parsed_date is None:
            return None  # Missing date → skip row

        if parsed_date.warning:
            warnings.append(f"date: {parsed_date.warning}")

        # ── Value date (optional) ─────────────────────────────────────────────
        value_date_result: ParsedDate | None = None
        if m.value_date is not None:
            raw_value_date = get(m.value_date)
            if raw_value_date:
                try:
                    value_date_result = parse_date(raw_value_date, dayfirst=self.dayfirst)
                except ValueError:
                    warnings.append("value_date: Could not parse, ignored")

        # ── Amount ───────────────────────────────────────────────────────────
        parsed_amount: ParsedAmount | None = None

        if m.debit is not None or m.credit is not None:
            # Split debit/credit columns
            raw_debit = get(m.debit) if m.debit is not None else None
            raw_credit = get(m.credit) if m.credit is not None else None
            try:
                parsed_amount = parse_split_amounts(raw_debit, raw_credit)
            except ValueError as e:
                warnings.append(f"amount: Split parse failed — {e}")
        elif m.amount is not None:
            # Single amount column
            raw_amount = get(m.amount)
            try:
                parsed_amount = parse_amount(raw_amount)
            except ValueError as e:
                warnings.append(f"amount: {e}")

        if parsed_amount is None:
            warnings.append("amount: Missing or null — row retained without amount")
            # Store as 0 with warning (don't skip — date may still be useful)
            amount_value = Decimal("0")
            direction = "D"
        else:
            amount_value = parsed_amount.value
            direction = parsed_amount.direction
            if parsed_amount.warning:
                warnings.append(f"amount: {parsed_amount.warning}")
            if parsed_amount.is_inferred:
                warnings.append("amount: Direction inferred (no explicit CR/DR marker)")

        # ── Balance ──────────────────────────────────────────────────────────
        balance_after: Decimal | None = None
        if m.balance is not None:
            raw_balance = get(m.balance)
            if raw_balance:
                try:
                    parsed_bal = parse_amount(raw_balance, default_direction="C")
                    balance_after = parsed_bal.value if parsed_bal else None
                except ValueError:
                    warnings.append("balance: Could not parse running balance")

        # ── Description ──────────────────────────────────────────────────────
        raw_description = get(m.description)
        description = _clean_description(raw_description)

        # ── Reference ────────────────────────────────────────────────────────
        reference = get(m.reference) or None

        # ── Currency ─────────────────────────────────────────────────────────
        currency = get(m.currency).upper() if m.currency is not None and get(m.currency) else None

        # ── Transaction type ─────────────────────────────────────────────────
        explicit_type_raw = get(m.transaction_type)
        transaction_type = _infer_transaction_type(
            explicit_raw=explicit_type_raw,
            description=description,
            direction=direction,
        )

        return ParsedTransaction(
            row_index=row_index,
            transaction_date=parsed_date.value,
            value_date=value_date_result.value if value_date_result else None,
            description=description,
            raw_description=raw_description if raw_description != description else None,
            reference_number=reference,
            transaction_type=transaction_type,
            amount=amount_value,
            direction=direction,
            balance_after=balance_after,
            currency=currency,
            parse_warnings=warnings,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 5: PostProcessor — validate, compute stats, build result
# ═══════════════════════════════════════════════════════════════════════════════

class PostProcessor:
    """
    Final validation and result assembly.
    - Infers statement date range from transactions
    - Detects dominant currency
    - Validates running balance continuity
    - Computes statistics
    """

    def process(
        self,
        transactions: list[ParsedTransaction],
        raw_headers: dict,
        encoding: str,
        delimiter: str,
        column_mapping: ColumnMapping,
        rows_skipped: int,
        format_warnings: list[dict],
    ) -> ParsedBankStatement:
        warnings = list(format_warnings)

        if not transactions:
            raise CSVNoDataRowsError(
                "No valid transaction rows found after parsing. "
                "Check that the file contains data rows below the header."
            )

        # ── Infer date range ──────────────────────────────────────────────────
        dates = [t.transaction_date for t in transactions]
        statement_from = min(dates)
        statement_to = max(dates)

        # ── Dominant currency ─────────────────────────────────────────────────
        currencies = [t.currency for t in transactions if t.currency]
        dominant_currency = _most_common(currencies) if currencies else None

        if len(set(currencies)) > 1:
            warnings.append({
                "field": "currency",
                "message": f"Multiple currencies detected: {set(currencies)}. "
                           f"Dominant: {dominant_currency}. "
                           "Per-row currency stored on each transaction.",
            })

        # ── Opening / closing balance from first/last transactions ────────────
        opening_balance = transactions[0].balance_after  # approximate
        closing_balance = transactions[-1].balance_after

        # ── Balance continuity check ──────────────────────────────────────────
        if all(t.balance_after is not None for t in transactions):
            warnings.extend(self._check_balance_continuity(transactions))

        # ── Row-level warnings ────────────────────────────────────────────────
        for tx in transactions:
            if tx.parse_warnings:
                warnings.append({
                    "field": f"row_{tx.row_index}",
                    "message": "; ".join(tx.parse_warnings),
                })

        return ParsedBankStatement(
            bank_name=None,             # Not typically in transaction CSV
            account_number=None,        # Would come from metadata rows
            account_holder=None,
            currency=dominant_currency,
            statement_from=statement_from,
            statement_to=statement_to,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            transactions=transactions,
            detected_encoding=encoding,
            detected_delimiter=delimiter,
            detected_format=_detect_bank_format(raw_headers),
            raw_headers=raw_headers,
            column_mapping=column_mapping,
            total_rows_parsed=len(transactions),
            total_rows_skipped=rows_skipped,
            warnings=warnings,
        )

    def _check_balance_continuity(
        self, transactions: list[ParsedTransaction]
    ) -> list[dict]:
        """Detect gaps in running balance (signs of missing rows)."""
        issues = []
        for i in range(1, len(transactions)):
            prev = transactions[i - 1]
            curr = transactions[i]
            if prev.balance_after is None or curr.balance_after is None:
                continue
            expected = (
                prev.balance_after + curr.amount
                if curr.direction == "C"
                else prev.balance_after - curr.amount
            )
            diff = abs(expected - curr.balance_after)
            if diff > Decimal("0.10"):  # Allow 10p rounding tolerance
                issues.append({
                    "field": f"balance_row_{curr.row_index}",
                    "message": (
                        f"Balance discontinuity at row {curr.row_index}: "
                        f"expected ~{expected:.2f}, got {curr.balance_after:.2f}. "
                        f"Diff: {diff:.2f}. Possible missing rows."
                    ),
                })
        return issues


# ═══════════════════════════════════════════════════════════════════════════════
# Main Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class CSVParser(BaseParser[ParsedBankStatement]):
    """
    Orchestrates the 5-stage CSV parsing pipeline.
    Entry point for all CSV bank statement parsing.
    """

    def __init__(self, max_rows: int = 100_000):
        self.max_rows = max_rows
        self._reader = FileReader()
        self._format_detector = FormatDetector()
        self._post_processor = PostProcessor()

    def parse(self, content: bytes) -> ParsedBankStatement:
        """
        Full pipeline: bytes → ParsedBankStatement.

        Args:
            content: Raw file bytes.

        Returns:
            ParsedBankStatement with transactions and quality metadata.

        Raises:
            CSVEncodingError: Cannot decode file.
            CSVMissingRequiredColumnsError: Mandatory columns absent.
            CSVNoDataRowsError: No valid data rows after filtering.
            CSVParseError: Unrecoverable structural error.
        """
        logger.info("CSV parsing started", content_size=len(content))

        # ── Stage 1: Decode ──────────────────────────────────────────────────
        text, encoding = self._reader.read(content)

        # ── Stage 2: Format Detection ────────────────────────────────────────
        delimiter, all_rows = self._format_detector.detect(text)

        if not all_rows:
            raise CSVParseError("File is empty or contains no parseable rows.")

        header_row_idx = self._format_detector.find_header_row(all_rows)
        header_row = all_rows[header_row_idx]
        data_rows = all_rows[header_row_idx + 1 :]

        column_mapping = self._format_detector.map_columns(header_row)
        self._format_detector.validate_mapping(column_mapping, header_row)

        raw_headers = {i: h.strip() for i, h in enumerate(header_row)}

        # ── Infer dayfirst from sample dates ─────────────────────────────────
        if column_mapping.date is not None:
            sample_dates = [
                row[column_mapping.date]
                for row in data_rows[:20]
                if len(row) > column_mapping.date
            ]
            dayfirst = infer_date_format_hint(sample_dates)
        else:
            dayfirst = True

        # ── Stage 3: Row Filtering ────────────────────────────────────────────
        row_filter = RowFilter(expected_col_count=len(header_row))
        row_parser = RowParser(mapping=column_mapping, dayfirst=dayfirst)

        transactions: list[ParsedTransaction] = []
        format_warnings: list[dict] = []

        # Enforce max row limit
        if len(data_rows) > self.max_rows:
            format_warnings.append({
                "field": "row_count",
                "message": (
                    f"File has {len(data_rows)} rows; truncated to {self.max_rows}. "
                    "Consider splitting large files."
                ),
            })
            data_rows = data_rows[: self.max_rows]

        # ── Stage 4: Row Parsing ─────────────────────────────────────────────
        for csv_row_index, row in enumerate(data_rows, start=header_row_idx + 2):
            if row_filter.should_skip(row, csv_row_index):
                continue

            try:
                parsed_tx = row_parser.parse(row, csv_row_index)
            except Exception as e:
                logger.warning(
                    "Unexpected row parse error",
                    row_index=csv_row_index,
                    error=str(e),
                )
                row_filter.skipped.append({
                    "row_index": csv_row_index,
                    "reason": f"parse_error: {e}",
                    "content": row[:3],
                })
                continue

            if parsed_tx is not None:
                transactions.append(parsed_tx)

        rows_skipped = len(row_filter.skipped)
        logger.info(
            "Row parsing complete",
            parsed=len(transactions),
            skipped=rows_skipped,
        )

        # ── Stage 5: Post-processing ─────────────────────────────────────────
        result = self._post_processor.process(
            transactions=transactions,
            raw_headers=raw_headers,
            encoding=encoding,
            delimiter=delimiter,
            column_mapping=column_mapping,
            rows_skipped=rows_skipped,
            format_warnings=format_warnings,
        )

        logger.info(
            "CSV parsing complete",
            transactions=result.total_rows_parsed,
            warnings=len(result.warnings),
            currency=result.currency,
            from_date=str(result.statement_from),
            to_date=str(result.statement_to),
        )

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def _clean_description(raw: str) -> str | None:
    """Normalize description text: collapse whitespace, strip control chars."""
    if not raw or not raw.strip():
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", raw)  # Strip control chars
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _infer_transaction_type(
    explicit_raw: str,
    description: str | None,
    direction: str,
) -> TransactionType:
    """
    Infer transaction type from explicit column value or description keywords.
    Falls back to CREDIT/DEBIT based on direction if no keywords match.
    """
    if explicit_raw:
        normalized = explicit_raw.strip().lower()
        type_map = {
            "credit": TransactionType.CREDIT,
            "cr": TransactionType.CREDIT,
            "debit": TransactionType.DEBIT,
            "dr": TransactionType.DEBIT,
            "transfer": TransactionType.TRANSFER,
            "fee": TransactionType.FEE,
            "interest": TransactionType.INTEREST,
        }
        if normalized in type_map:
            return type_map[normalized]

    if description:
        for pattern, tx_type in TYPE_KEYWORD_MAP:
            if pattern.search(description):
                return tx_type

    # Fall back to direction-based type
    return TransactionType.CREDIT if direction == "C" else TransactionType.DEBIT


def _most_common(items: list[str]) -> str:
    """Return the most frequently occurring item."""
    return max(set(items), key=items.count)


def _detect_bank_format(raw_headers: dict) -> str:
    """
    Attempt to identify the bank/format from header patterns.
    Returns a format identifier string.

    TODO: Add more bank-specific format fingerprints as needed.
    """
    headers_lower = {v.lower() for v in raw_headers.values()}

    if "narration" in headers_lower and "chq./ref.no." in headers_lower:
        return "HDFC_STANDARD"
    if "transaction remarks" in headers_lower:
        return "ICICI_STANDARD"
    if "particulars" in headers_lower and "cheque no" in headers_lower:
        return "SBI_STANDARD"
    if "memo" in headers_lower and "amount" in headers_lower:
        return "CHASE_US"
    if "description" in headers_lower and "debit" in headers_lower and "credit" in headers_lower:
        return "GENERIC_SPLIT"
    if "description" in headers_lower and "amount" in headers_lower:
        return "GENERIC_SINGLE"

    return "UNKNOWN"
