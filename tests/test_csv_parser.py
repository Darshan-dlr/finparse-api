"""
Tests for the CSV Parser pipeline — covers all edge cases.
Run with: pytest tests/test_csv_parser.py -v
"""
import pytest
from decimal import Decimal
from datetime import date
from pathlib import Path

from app.parsers.csv_parser import CSVParser, ParsedBankStatement
from app.utils.amount_parser import parse_amount, parse_split_amounts
from app.utils.date_parser import parse_date

SAMPLES = Path(__file__).parent / "sample_files"


# ═══════════════════════════════════════════════════════════════════════════════
# Amount Parser Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAmountParser:

    def test_standard_format(self):
        result = parse_amount("1234.56")
        assert result.value == Decimal("1234.56")

    def test_standard_with_comma_thousands(self):
        result = parse_amount("1,234.56")
        assert result.value == Decimal("1234.56")

    def test_european_decimal_comma(self):
        result = parse_amount("1.234,56")
        assert result.value == Decimal("1234.56")

    def test_european_no_thousands(self):
        result = parse_amount("1234,56")
        assert result.value == Decimal("1234.56")

    def test_indian_grouping(self):
        result = parse_amount("1,23,456.78")
        assert result.value == Decimal("123456.78")

    def test_parenthetical_negative(self):
        result = parse_amount("(1,234.56)")
        assert result.value == Decimal("1234.56")
        assert result.direction == "D"
        assert result.is_inferred == False

    def test_dr_suffix(self):
        result = parse_amount("500.00 DR")
        assert result.value == Decimal("500.00")
        assert result.direction == "D"

    def test_cr_suffix(self):
        result = parse_amount("500.00 CR")
        assert result.direction == "C"

    def test_currency_symbol_prefix(self):
        result = parse_amount("$1,234.56")
        assert result.value == Decimal("1234.56")

    def test_rupee_symbol(self):
        result = parse_amount("₹1,23,456.00")
        assert result.value == Decimal("123456.00")

    def test_null_dash(self):
        assert parse_amount("-") is None

    def test_null_empty(self):
        assert parse_amount("") is None

    def test_null_na(self):
        assert parse_amount("N/A") is None

    def test_explicit_negative(self):
        result = parse_amount("-500.00")
        assert result.value == Decimal("500.00")
        assert result.direction == "D"

    def test_split_amounts_debit_only(self):
        result = parse_split_amounts("500.00", "")
        assert result.value == Decimal("500.00")
        assert result.direction == "D"

    def test_split_amounts_credit_only(self):
        result = parse_split_amounts("", "1000.00")
        assert result.direction == "C"

    def test_split_amounts_both_empty(self):
        assert parse_split_amounts("", "") is None

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_amount("not-a-number")


# ═══════════════════════════════════════════════════════════════════════════════
# Date Parser Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDateParser:

    def test_iso_format(self):
        result = parse_date("2026-05-29")
        assert result.value == date(2026, 5, 29)
        assert result.is_ambiguous == False

    def test_uk_format_dayfirst(self):
        result = parse_date("29/05/2026", dayfirst=True)
        assert result.value == date(2026, 5, 29)

    def test_us_format_monthfirst(self):
        result = parse_date("05/29/2026", dayfirst=False)
        assert result.value == date(2026, 5, 29)

    def test_short_year(self):
        result = parse_date("29/05/26", dayfirst=True)
        assert result.value == date(2026, 5, 29)

    def test_month_name(self):
        result = parse_date("29-May-2026")
        assert result.value == date(2026, 5, 29)

    def test_month_name_full(self):
        result = parse_date("May 29, 2026")
        assert result.value == date(2026, 5, 29)

    def test_timestamp_stripped(self):
        result = parse_date("2026-05-29 14:30:00")
        assert result.value == date(2026, 5, 29)

    def test_iso_timestamp_stripped(self):
        result = parse_date("2026-05-29T14:30:00Z")
        assert result.value == date(2026, 5, 29)

    def test_excel_serial(self):
        # Excel serial 46044 = 2026-01-30 approximately
        result = parse_date("46000")
        assert result.is_excel_serial == True
        assert result.value is not None

    def test_ambiguous_date_flagged(self):
        result = parse_date("04/05/2026", dayfirst=True)
        assert result.is_ambiguous == True
        assert result.warning is not None

    def test_unambiguous_high_day(self):
        result = parse_date("29/05/2026", dayfirst=True)
        assert result.is_ambiguous == False

    def test_null_dash(self):
        assert parse_date("-") is None

    def test_null_empty(self):
        assert parse_date("") is None

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_date("not-a-date")


# ═══════════════════════════════════════════════════════════════════════════════
# CSV Parser — Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCSVParser:

    @pytest.fixture
    def parser(self):
        return CSVParser(max_rows=10_000)

    def _load_sample(self, filename: str) -> bytes:
        return (SAMPLES / filename).read_bytes()

    def test_standard_statement_parses(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result: ParsedBankStatement = parser.parse(content)

        assert result.total_rows_parsed > 0
        assert result.statement_from is not None
        assert result.statement_to is not None
        assert result.detected_delimiter == ","

    def test_standard_statement_transaction_count(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        # Should parse the data rows and skip "Opening Balance" and "Closing Balance"
        assert result.total_rows_parsed >= 10

    def test_standard_statement_directions(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        directions = {tx.direction for tx in result.transactions}
        assert "C" in directions
        assert "D" in directions

    def test_standard_amounts_are_positive(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        for tx in result.transactions:
            assert tx.amount >= 0, f"Negative amount found: {tx.amount} at row {tx.row_index}"

    def test_european_semicolon_delimiter(self, parser):
        content = self._load_sample("european_semicolon_statement.csv")
        result = parser.parse(content)
        assert result.detected_delimiter == ";"
        assert result.total_rows_parsed > 0

    def test_hdfc_style_with_metadata_rows(self, parser):
        content = self._load_sample("hdfc_style_statement.csv")
        result = parser.parse(content)
        assert result.total_rows_parsed > 0
        # Verify "Total" row was skipped
        assert result.total_rows_skipped >= 1

    def test_hdfc_format_detected(self, parser):
        content = self._load_sample("hdfc_style_statement.csv")
        result = parser.parse(content)
        assert "HDFC" in result.detected_format or result.detected_format != "UNKNOWN"

    def test_empty_file_raises(self, parser):
        from app.core.exceptions import CSVParseError
        with pytest.raises(CSVParseError):
            parser.parse(b"")

    def test_too_small_file_raises(self, parser):
        from app.core.exceptions import CSVParseError
        with pytest.raises((CSVParseError, Exception)):
            parser.parse(b"a,b\n")

    def test_result_has_parser_version(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        assert result.parser_version.startswith("csv-parser-v")

    def test_raw_headers_preserved(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        assert isinstance(result.raw_headers, dict)
        assert len(result.raw_headers) > 0

    def test_row_index_preserved(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        for tx in result.transactions:
            assert tx.row_index > 0, "row_index should be 1-indexed CSV line number"

    def test_max_rows_enforced(self):
        # Create a CSV with 200 data rows
        header = "Date,Description,Amount\n"
        rows = "".join(f"01/05/2026,Test Transaction {i},100.00\n" for i in range(200))
        content = (header + rows).encode()

        parser = CSVParser(max_rows=50)
        result = parser.parse(content)
        assert result.total_rows_parsed <= 50
        assert any("truncated" in str(w.get("message", "")) for w in result.warnings)

    def test_description_cleaned(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        for tx in result.transactions:
            if tx.description:
                assert "\x00" not in tx.description
                assert "\n" not in tx.description

    def test_date_range_inferred(self, parser):
        content = self._load_sample("standard_bank_statement.csv")
        result = parser.parse(content)
        assert result.statement_from <= result.statement_to
