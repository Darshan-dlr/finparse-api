import re
from app.models.enums import TransactionType

# ═══════════════════════════════════════════════════════════════════════════════
# CSV Parser Constants
# ═══════════════════════════════════════════════════════════════════════════════

CSV_PARSER_VERSION = "csv-parser-v1.0.0"

CSV_MIN_DATA_ROWS = 1

CSV_SKIP_ROW_PATTERNS = re.compile(
    r"^\s*(total|balance|closing|opening|subtotal|grand\s+total|net\s+total"
    r"|brought\s+forward|carried\s+forward|statement\s+summary|page\s+\d+)\b",
    re.IGNORECASE,
)

CSV_TYPE_KEYWORD_MAP: list[tuple[re.Pattern, TransactionType]] = [
    (re.compile(r"\b(transfer|trf|trsf|neft|rtgs|imps|upi)\b", re.I), TransactionType.TRANSFER),
    (re.compile(r"\b(fee|charge|penalty|fine|commission)\b", re.I), TransactionType.FEE),
    (re.compile(r"\b(interest|int earned|int paid)\b", re.I), TransactionType.INTEREST),
    (re.compile(r"\b(atm|cash withdrawal|cash deposit)\b", re.I), TransactionType.DEBIT),
    (re.compile(r"\b(salary|payroll|stipend)\b", re.I), TransactionType.CREDIT),
]

# ═══════════════════════════════════════════════════════════════════════════════
# PDF Parser Constants
# ═══════════════════════════════════════════════════════════════════════════════

PDF_PARSER_VERSION = "pdf-parser-v1.0.0"

PDF_INV_NUM_PATTERNS = [
    re.compile(r"(?:invoice[ \t]*number|invoice[ \t]*no\.?|invoice[ \t]*#|inv[ \t]*no\.?|inv[ \t]*#|bill[ \t]*no\.?|invoice[ \t]*id)[ \t]*[:#-]?[ \t]*([A-Za-z0-9\-_]+)", re.IGNORECASE),
    re.compile(r"inv-?([A-Za-z0-9\-_]+)", re.IGNORECASE),
]

PDF_INV_DATE_PATTERNS = [
    re.compile(r"(?:invoice[ \t]*date|issue[ \t]*date|billing[ \t]*date|date[ \t]*of[ \t]*issue|date)[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE)
]

PDF_DUE_DATE_PATTERNS = [
    re.compile(r"(?:due[ \t]*date|payment[ \t]*due|due[ \t]*by|pay[ \t]*by|expiry[ \t]*date)[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE)
]

PDF_SUBTOTAL_PATTERNS = [
    re.compile(r"\b(?:subtotal|sub-total|net[ \t]*amount|net[ \t]*total)\b[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE)
]

PDF_TAX_PATTERNS = [
    re.compile(r"\b(?:tax|vat|gst|sales[ \t]*tax|tax[ \t]*amount|vat[ \t]*amount)\b[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE)
]

PDF_DISCOUNT_PATTERNS = [
    re.compile(r"\b(?:discount|rebate|promo|discount[ \t]*amount)\b[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE)
]

PDF_TOTAL_PATTERNS = [
    re.compile(r"\b(?:total|amount[ \t]*due|total[ \t]*due|grand[ \t]*total|balance[ \t]*due|invoice[ \t]*total)\b[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE)
]

PDF_VENDOR_PATTERNS = [
    re.compile(r"(?:vendor|seller|billed[ \t]*from|service[ \t]*provider)[ \t]*[:\-]?[ \t]*([^\n\r]+)", re.IGNORECASE),
    re.compile(r"(?:from)[ \t]*[:\-][ \t]*([^\n\r]+)", re.IGNORECASE),
]

PDF_CURRENCY_MAP = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "¥": "JPY",
    "USD": "USD",
    "EUR": "EUR",
    "GBP": "GBP",
    "INR": "INR",
    "JPY": "JPY",
}

PDF_HEADER_MAPS = {
    "description": ["description", "item", "details", "desc", "particulars", "product", "service", "name"],
    "quantity": ["qty", "quantity", "qty.", "units", "count", "qnty"],
    "unit_price": ["unit price", "price", "unit_price", "rate", "cost", "unit cost", "price/unit"],
    "line_total": ["total", "amount", "line total", "total amount", "subtotal", "value"],
    "sku": ["sku", "item code", "code", "part no", "part number", "id", "sku/code"],
    "unit_of_measure": ["unit", "uom", "measure"],
    "tax_amount": ["tax", "vat", "gst", "tax amount", "tax_amount"],
    "tax_rate": ["tax rate", "tax %", "vat %", "gst %", "tax_rate"]
}
