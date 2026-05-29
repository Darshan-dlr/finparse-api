"""
Amount parser — handles every real-world amount format found in bank CSVs.

Supported formats:
  Standard:      1234.56  |  1,234.56
  European:      1.234,56  |  1234,56
  Indian:        1,23,456.78
  Accounting:    (1234.56)  |  (1,234.56)  ← negative
  Suffix sign:   1234.56 DR  |  1234.56 CR
  Symbol prefix: $1,234.56  |  £500.00  |  €1.234,56  |  ₹1,23,456
  Missing/null:  -  |  N/A  |  nil  |  (empty)
"""
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass

# Currency symbols to strip
CURRENCY_SYMBOLS = r"[£$€₹¥₩₺฿₪₨]"

# Words that indicate zero/missing values
NULL_VALUE_PATTERNS = {"", "-", "--", "n/a", "na", "nil", "none", "null", "0.00"}

# Suffix patterns indicating debit/credit direction
DEBIT_SUFFIXES = {"dr", "debit", "d", "withdrawal", "withdraw"}
CREDIT_SUFFIXES = {"cr", "credit", "c", "deposit", "dep"}


@dataclass
class ParsedAmount:
    value: Decimal
    direction: str          # 'C' | 'D'
    is_inferred: bool       # True if direction was guessed, not explicit
    original: str           # raw string from CSV
    warning: str | None     # non-fatal parsing note


def parse_amount(
    raw: str,
    default_direction: str = "D",
) -> ParsedAmount | None:
    """
    Parse a raw amount string into a normalized ParsedAmount.

    Args:
        raw:               The raw string from the CSV cell.
        default_direction: Fall-back direction when no sign info ('C' or 'D').

    Returns:
        ParsedAmount, or None if the value represents a missing/null amount.

    Raises:
        ValueError: If the string is non-null but unparseable.
    """
    original = raw
    warning: str | None = None

    # ── Normalize whitespace ────────────────────────────────────────────────
    s = raw.strip()

    # ── Null / missing check ─────────────────────────────────────────────────
    if s.lower() in NULL_VALUE_PATTERNS:
        return None

    # ── Detect negative from parentheses: (1,234.56) ────────────────────────
    is_negative = s.startswith("(") and s.endswith(")")
    if is_negative:
        s = s[1:-1].strip()

    # ── Strip currency symbols ───────────────────────────────────────────────
    s = re.sub(CURRENCY_SYMBOLS, "", s).strip()

    # ── Extract and remove direction suffixes: "DR", "CR", "D", "C" ─────────
    explicit_direction: str | None = None
    is_inferred = True

    suffix_match = re.search(r"\s+([a-zA-Z]+)\s*$", s)
    if suffix_match:
        suffix = suffix_match.group(1).lower()
        if suffix in DEBIT_SUFFIXES:
            explicit_direction = "D"
            s = s[: suffix_match.start()].strip()
        elif suffix in CREDIT_SUFFIXES:
            explicit_direction = "C"
            s = s[: suffix_match.start()].strip()

    if explicit_direction:
        is_inferred = False
    elif is_negative:
        explicit_direction = "D"
        is_inferred = False

    direction = explicit_direction or default_direction

    # ── Detect number format (European vs Standard vs Indian) ───────────────
    s = _normalize_number_format(s, original)

    # ── Final parse ───────────────────────────────────────────────────────────
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Cannot parse amount from: {original!r}")

    if value < 0:
        # Handle explicit negative sign (e.g., "-500.00")
        value = abs(value)
        direction = "D"
        is_inferred = False

    return ParsedAmount(
        value=value,
        direction=direction,
        is_inferred=is_inferred,
        original=original,
        warning=warning,
    )


def _normalize_number_format(raw: str, original: str) -> str:
    """
    Detect and normalize number format to plain decimal string.

    Detection logic:
    1. If the string matches European format (comma as decimal separator):
       e.g., "1.234,56" or "1234,56" → "1234.56"
    2. Indian grouping: e.g., "1,23,456.78" → "123456.78"
    3. Standard: "1,234.56" → "1234.56"
    4. Plain: "1234.56" → unchanged
    """
    s = raw.strip()

    # Remove any spaces used as thousand separators (e.g., "1 234,56")
    s = s.replace(" ", "")

    # ── European format detection ─────────────────────────────────────────────
    # Pattern: ends with comma + 1 or 2 digits, AND dots used as thousand sep
    # e.g., "1.234,56"  →  last separator is comma
    if re.search(r",\d{1,2}$", s) and "." in s:
        # Last comma is decimal, dots are thousands → strip dots, replace comma
        s = s.replace(".", "").replace(",", ".")
        return s

    # ── European without thousand sep: "1234,56" ─────────────────────────────
    # Has exactly one comma and no dot, and ≤ 2 digits after comma
    if "," in s and "." not in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # Looks like decimal comma
            return s.replace(",", ".")
        else:
            # Comma is thousand separator (e.g., "1,234,567")
            return s.replace(",", "")

    # ── Standard / Indian format: strip commas ────────────────────────────────
    # Both use comma as thousand separator; Indian has irregular grouping
    s = s.replace(",", "")

    return s


def parse_split_amounts(
    debit_raw: str | None,
    credit_raw: str | None,
) -> ParsedAmount | None:
    """
    Parse bank CSVs that have separate Debit and Credit columns.
    Returns a single ParsedAmount with the appropriate direction.

    Many bank formats have two separate columns rather than a signed amount:
        | Date | Description | Debit | Credit | Balance |
    """
    debit = parse_amount(debit_raw or "", default_direction="D") if debit_raw else None
    credit = parse_amount(credit_raw or "", default_direction="C") if credit_raw else None

    if debit and credit:
        # Both columns filled — unusual, take the larger one and warn
        # (could be a formatting artifact)
        result = debit if debit.value >= credit.value else credit
        result.warning = "Both debit and credit columns were non-empty; larger value used."
        return result

    if debit:
        debit.direction = "D"
        debit.is_inferred = False
        return debit

    if credit:
        credit.direction = "C"
        credit.is_inferred = False
        return credit

    return None  # Both empty → missing transaction amount
