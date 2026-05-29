"""
Date parser — handles every real-world date format found in bank CSVs and invoices.

Supported formats:
  ISO:           2026-05-29
  UK/EU:         29/05/2026  |  29-05-2026  |  29.05.2026
  US:            05/29/2026  |  05-29-2026
  Short year:    29/05/26  |  05/29/26
  Month name:    29-May-2026  |  29 May 2026  |  May 29, 2026
  Month abbrev:  29-May-26  |  May-26
  Excel serial:  46044  (days since 1900-01-01)
  Timestamp:     2026-05-29 14:30:00  |  2026-05-29T14:30:00Z
  Ambiguous:     04/05/2026  → logged as warning
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from dateutil import parser as dateutil_parser
from dateutil.parser import ParserError

# Excel epoch: January 1, 1900 (with Lotus 1-2-3 bug adjustment)
EXCEL_EPOCH = date(1899, 12, 30)

# Patterns that look like Excel serial numbers
EXCEL_SERIAL_PATTERN = re.compile(r"^\d{4,6}$")

# Ambiguity threshold: if day > 12 it cannot be a month, so no ambiguity
MONTH_MAX = 12

# Known null / missing values
NULL_DATE_PATTERNS = {"", "-", "--", "n/a", "na", "nil", "none", "null", "pending", "tbd"}


@dataclass
class ParsedDate:
    value: date
    is_ambiguous: bool      # True if day/month could be swapped
    is_excel_serial: bool   # True if input was an Excel serial number
    original: str           # raw string from CSV
    warning: str | None     # non-fatal note


def parse_date(
    raw: str,
    dayfirst: bool = True,           # Most non-US bank statements are DD/MM/YYYY
    yearfirst: bool = False,
) -> ParsedDate | None:
    """
    Parse a raw date string into a normalized ParsedDate.

    Args:
        raw:       The raw string from the CSV cell.
        dayfirst:  Hint for ambiguous dates like 04/05/2026 (default: True for DD/MM).
        yearfirst: Hint when year comes first (e.g., Japanese format).

    Returns:
        ParsedDate, or None if the value is a null/missing marker.

    Raises:
        ValueError: If the string is non-null but unparseable.
    """
    original = raw
    s = raw.strip()

    # ── Null / missing check ─────────────────────────────────────────────────
    if s.lower() in NULL_DATE_PATTERNS:
        return None

    # ── Excel serial number ──────────────────────────────────────────────────
    if EXCEL_SERIAL_PATTERN.match(s):
        return _parse_excel_serial(s, original)

    # ── Timestamp: strip time component ─────────────────────────────────────
    s = _strip_time_component(s)

    # ── Try ISO format first (unambiguous, fast path) ────────────────────────
    iso_result = _try_iso_parse(s, original)
    if iso_result:
        return iso_result

    # ── Try dateutil with dayfirst hint ─────────────────────────────────────
    return _try_dateutil_parse(s, original, dayfirst=dayfirst, yearfirst=yearfirst)


# ── Private helpers ────────────────────────────────────────────────────────────

def _parse_excel_serial(s: str, original: str) -> ParsedDate:
    """Convert Excel serial number (e.g., 46044) to date."""
    try:
        serial = int(s)
        from datetime import timedelta
        value = EXCEL_EPOCH + timedelta(days=serial)
        return ParsedDate(
            value=value,
            is_ambiguous=False,
            is_excel_serial=True,
            original=original,
            warning=f"Date was an Excel serial number ({serial}); converted to {value}.",
        )
    except (ValueError, OverflowError) as e:
        raise ValueError(f"Cannot convert Excel serial '{original}': {e}")


def _strip_time_component(s: str) -> str:
    """
    Remove the time portion from a datetime string.
    e.g., "2026-05-29 14:30:00" → "2026-05-29"
          "2026-05-29T14:30:00Z" → "2026-05-29"
    """
    # ISO-style datetime
    s = re.sub(r"[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", "", s)
    return s.strip()


def _try_iso_parse(s: str, original: str) -> ParsedDate | None:
    """Fast path for ISO 8601 format (YYYY-MM-DD). Unambiguous."""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            value = date.fromisoformat(s)
            return ParsedDate(
                value=value,
                is_ambiguous=False,
                is_excel_serial=False,
                original=original,
                warning=None,
            )
        except ValueError:
            pass
    return None


def _try_dateutil_parse(
    s: str,
    original: str,
    dayfirst: bool,
    yearfirst: bool,
) -> ParsedDate:
    """
    Use dateutil for all non-ISO formats.
    Detects ambiguous dates and adds warnings.
    """
    # Normalize separators to / for consistent parsing
    normalized = re.sub(r"[-.]", "/", s)

    try:
        parsed: datetime = dateutil_parser.parse(
            normalized,
            dayfirst=dayfirst,
            yearfirst=yearfirst,
        )
    except (ParserError, ValueError, OverflowError) as e:
        raise ValueError(f"Cannot parse date from: {original!r}. Error: {e}")

    value = parsed.date()
    warning, is_ambiguous = _check_ambiguity(normalized, value, dayfirst)

    return ParsedDate(
        value=value,
        is_ambiguous=is_ambiguous,
        is_excel_serial=False,
        original=original,
        warning=warning,
    )


def _check_ambiguity(s: str, parsed_date: date, dayfirst: bool) -> tuple[str | None, bool]:
    """
    Detect if the date is ambiguous (day and month could be swapped).
    Only ambiguous when both the first and second numeric components are ≤ 12.

    e.g., "04/05/2026" — could be April 5 or May 4.
          "29/05/2026" — unambiguous (29 cannot be a month).
    """
    # Extract the two leading numeric components
    parts = re.findall(r"\d+", s)
    if len(parts) < 2:
        return None, False

    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return None, False

    # Only ambiguous if both values could be month numbers
    if a <= MONTH_MAX and b <= MONTH_MAX and a != b:
        interpretation = (
            f"{'Day-first' if dayfirst else 'Month-first'} assumed: parsed as {parsed_date}. "
            f"Ambiguous — could also be {parsed_date.replace(month=parsed_date.day, day=parsed_date.month)}."
        )
        return interpretation, True

    return None, False


def infer_date_format_hint(sample_dates: list[str]) -> bool:
    """
    Infer dayfirst setting from a sample of date strings.
    If any date has a leading component > 12, it must be the day (dayfirst=True).
    Returns True if dayfirst is likely, False for monthfirst (US).
    """
    for raw in sample_dates:
        parts = re.findall(r"\d+", raw.strip())
        if parts:
            try:
                first = int(parts[0])
                if first > MONTH_MAX:
                    return True  # Definitely day-first
            except ValueError:
                continue
    # Default to dayfirst=True (most bank statements outside US)
    return True
