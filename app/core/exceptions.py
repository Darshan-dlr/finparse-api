"""
All custom application exceptions with structured error codes.
Every exception maps to a specific HTTP status code and error payload.
"""
from typing import Any


class FinParseException(Exception):
    """Base exception for all application errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        self.message = message
        self.detail = detail or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "message": self.message,
            **self.detail,
        }


# ── Upload / File Validation Exceptions (Stage 1) ─────────────────────────────

class EmptyFileError(FinParseException):
    status_code = 400
    error_code = "EMPTY_FILE"


class InvalidExtensionError(FinParseException):
    status_code = 400
    error_code = "INVALID_EXTENSION"


class FileTooLargeError(FinParseException):
    status_code = 413
    error_code = "FILE_TOO_LARGE"


class UploadIncompleteError(FinParseException):
    status_code = 400
    error_code = "UPLOAD_INCOMPLETE"


# ── File Integrity Exceptions (Stage 2) ───────────────────────────────────────

class DuplicateFileError(FinParseException):
    """Raised when a file with the same SHA-256 checksum already exists."""
    status_code = 409
    error_code = "DUPLICATE_FILE"

    def __init__(self, existing_document_id: str):
        super().__init__(
            message="This file has already been uploaded.",
            detail={
                "existing_document_id": existing_document_id,
                "hint": "Pass ?allow_reprocess=true to re-parse the existing file.",
            },
        )


class FileMimeTypeMismatchError(FinParseException):
    status_code = 415
    error_code = "FILE_TYPE_MISMATCH"


class UnsupportedFormatError(FinParseException):
    status_code = 415
    error_code = "UNSUPPORTED_FORMAT"


# ── PDF-Specific Exceptions (Stage 3 — PDF) ───────────────────────────────────

class PDFPasswordRequiredError(FinParseException):
    status_code = 422
    error_code = "PDF_PASSWORD_REQUIRED"

    def __init__(self, document_id: str | None = None):
        detail = {"hint": "Re-upload with the 'pdf_password' form field."}
        if document_id:
            detail["document_id"] = document_id
        super().__init__(
            message="This PDF is password-protected. A password is required.",
            detail=detail,
        )


class PDFWrongPasswordError(FinParseException):
    status_code = 422
    error_code = "PDF_WRONG_PASSWORD"


class PDFCorruptedError(FinParseException):
    status_code = 422
    error_code = "PDF_CORRUPTED"


class PDFTooManyPagesError(FinParseException):
    status_code = 422
    error_code = "PDF_TOO_MANY_PAGES"


class OCRFailedError(FinParseException):
    status_code = 422
    error_code = "OCR_FAILED"


# ── CSV-Specific Exceptions (Stage 3 — CSV) ───────────────────────────────────

class CSVParseError(FinParseException):
    status_code = 422
    error_code = "CSV_PARSE_ERROR"


class CSVEncodingError(FinParseException):
    status_code = 422
    error_code = "CSV_ENCODING_ERROR"


class CSVNoDataRowsError(FinParseException):
    status_code = 422
    error_code = "CSV_NO_DATA_ROWS"


class CSVMissingRequiredColumnsError(FinParseException):
    status_code = 422
    error_code = "CSV_MISSING_REQUIRED_COLUMNS"

    def __init__(self, missing: list[str], available: list[str]):
        super().__init__(
            message=f"Required columns not found: {missing}",
            detail={
                "missing_columns": missing,
                "available_columns": available,
                "hint": "Ensure your CSV has at least a date column and an amount column.",
            },
        )


# ── Parsing Result Exceptions ─────────────────────────────────────────────────

class PartialParseError(FinParseException):
    """Raised when parsing succeeds but with warnings (non-fatal)."""
    status_code = 207
    error_code = "PARTIAL_PARSE"
