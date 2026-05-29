import enum

class DocumentType(str, enum.Enum):
    INVOICE = "invoice"
    BANK_STATEMENT = "bank_statement"


class TransactionType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    TRANSFER = "transfer"
    FEE = "fee"
    INTEREST = "interest"
    UNKNOWN = "unknown"


class JobStatus(str, enum.Enum):
    PENDING = "pending"         # Uploaded, worker hasn't picked it up yet
    PROCESSING = "processing"   # Worker is actively parsing
    COMPLETED = "completed"     # Fully parsed, all fields extracted
    FAILED = "failed"           # Parsing failed entirely
    PARTIAL = "partial"         # Parsed with warnings (missing fields, guessed values)
