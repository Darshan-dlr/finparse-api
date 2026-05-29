"""
3-Stage file validation pipeline.

Stage 1 — HTTP Layer:      extension whitelist, filename sanitization, size check
Stage 2 — File Integrity:  magic bytes / MIME check, SHA-256, zero-byte check, duplicate check
Stage 3 — Content Check:   PDF-specific validations | CSV format pre-check
"""
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import magic as _magic
    _MAGIC_AVAILABLE = True
except (ImportError, OSError):
    _magic = None  # type: ignore
    _MAGIC_AVAILABLE = False

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from app.config import get_settings
from app.core.exceptions import (
    CSVParseError,
    DuplicateFileError,
    EmptyFileError,
    FileMimeTypeMismatchError,
    FileTooLargeError,
    InvalidExtensionError,
    PDFCorruptedError,
    PDFPasswordRequiredError,
    PDFTooManyPagesError,
    PDFWrongPasswordError,
    UnsupportedFormatError,
    UploadIncompleteError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ── Allowed file types ────────────────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".pdf", ".csv", ".xlsx"}

ALLOWED_MIMES = {
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "text/plain": ".csv",    # Some systems send CSV as text/plain
    "application/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xlsx",
}

PDF_MIMES = {"application/pdf"}
CSV_MIMES = {"text/csv", "text/plain", "application/csv"}
EXCEL_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}

# ── Filename sanitization ─────────────────────────────────────────────────────
SAFE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9._\-]")


@dataclass
class ValidatedFile:
    """Result of passing all 3 validation stages."""
    content: bytes
    checksum_sha256: str
    detected_mime: str
    safe_filename: str
    original_filename: str
    file_size_bytes: int
    file_extension: str          # '.pdf' | '.csv'

    # PDF-specific
    pdf_page_count: int | None = None
    pdf_is_encrypted: bool = False
    pdf_encryption_type: str | None = None
    pdf_password_used: bool = False
    ocr_needed: bool = False
    scanned_pages: list[int] | None = None

    # CSV-specific
    csv_encoding_hint: str | None = None
    csv_is_excel: bool = False   # True if .xlsx auto-converted to CSV


class FileValidator:
    """
    Runs all 3 validation stages on an uploaded file.
    Fail fast — cheapest checks first.
    """

    def __init__(self, checksum_lookup_fn=None):
        """
        Args:
            checksum_lookup_fn: Async callable(checksum: str) → document_id | None
                                Used for duplicate detection. Pass None to skip.
        """
        self._checksum_lookup = checksum_lookup_fn

    async def validate(
        self,
        file: UploadFile,
        pdf_password: str | None = None,
        allow_reprocess: bool = False,
    ) -> ValidatedFile:
        """
        Run all 3 stages. Returns ValidatedFile on success.
        Raises a FinParseException subclass on any failure.
        """
        # ── Stage 1: HTTP Layer ───────────────────────────────────────────────
        safe_filename = self._sanitize_filename(file.filename or "upload")
        self._check_extension(safe_filename)
        content = await self._read_with_size_limit(file, safe_filename)

        # ── Stage 2: Integrity ────────────────────────────────────────────────
        self._check_empty(content, safe_filename)
        checksum = self._compute_checksum(content)
        await self._check_duplicate(checksum, allow_reprocess)
        detected_mime = self._detect_mime(content)
        self._check_mime_vs_extension(detected_mime, safe_filename)

        # ── Resolve actual extension from MIME (handles .xlsx → .csv) ────────
        actual_extension = ALLOWED_MIMES.get(detected_mime, Path(safe_filename).suffix.lower())

        logger.info(
            "File integrity validated",
            filename=safe_filename,
            mime=detected_mime,
            size=len(content),
            checksum=checksum[:12] + "...",
        )

        # ── Stage 3: Content-specific validation ──────────────────────────────
        if detected_mime in PDF_MIMES or actual_extension == ".pdf":
            return await self._validate_pdf(
                content=content,
                checksum=checksum,
                detected_mime=detected_mime,
                safe_filename=safe_filename,
                original_filename=file.filename or safe_filename,
                pdf_password=pdf_password,
            )

        elif detected_mime in CSV_MIMES or actual_extension == ".csv":
            return self._validate_csv(
                content=content,
                checksum=checksum,
                detected_mime=detected_mime,
                safe_filename=safe_filename,
                original_filename=file.filename or safe_filename,
            )

        elif detected_mime in EXCEL_MIMES or actual_extension == ".xlsx":
            return self._validate_excel(
                content=content,
                checksum=checksum,
                safe_filename=safe_filename,
                original_filename=file.filename or safe_filename,
            )

        raise UnsupportedFormatError(
            f"File type '{detected_mime}' is not supported. "
            f"Supported types: PDF, CSV, XLSX."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 1 — HTTP Layer
    # ══════════════════════════════════════════════════════════════════════════

    def _sanitize_filename(self, filename: str) -> str:
        """
        Prevent path traversal and strip unsafe characters.
        e.g., "../../etc/passwd.pdf" → "passwd.pdf"
              "my invoice (2026).pdf" → "my_invoice__2026_.pdf"
        """
        # Strip directory components (path traversal prevention)
        name = Path(filename).name
        # Replace unsafe characters
        name = SAFE_FILENAME_PATTERN.sub("_", name)
        # Enforce length limit
        if len(name) > 255:
            stem = Path(name).stem[:200]
            suffix = Path(name).suffix
            name = stem + suffix
        return name or "upload"

    def _check_extension(self, filename: str) -> None:
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise InvalidExtensionError(
                f"Extension '{ext}' is not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

    async def _read_with_size_limit(self, file: UploadFile, filename: str) -> bytes:
        """
        Stream-read file while enforcing size limit.
        Avoids loading > MAX_SIZE bytes into memory at all.
        """
        # Determine size limit based on expected type
        ext = Path(filename).suffix.lower()
        max_bytes = (
            settings.max_pdf_size_bytes if ext == ".pdf"
            else settings.max_csv_size_bytes
        )

        chunks = []
        total = 0

        try:
            while True:
                chunk = await file.read(65536)  # 64 KB chunks
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FileTooLargeError(
                        f"File exceeds maximum allowed size of "
                        f"{max_bytes // (1024*1024)} MB for {ext} files.",
                        detail={"max_size_mb": max_bytes // (1024 * 1024)},
                    )
                chunks.append(chunk)
        except FileTooLargeError:
            raise
        except Exception as e:
            raise UploadIncompleteError(
                f"File upload was interrupted: {e}"
            )

        return b"".join(chunks)

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 2 — File Integrity
    # ══════════════════════════════════════════════════════════════════════════

    def _check_empty(self, content: bytes, filename: str) -> None:
        if len(content) == 0:
            raise EmptyFileError(f"The uploaded file '{filename}' is empty (0 bytes).")

    def _compute_checksum(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    async def _check_duplicate(self, checksum: str, allow_reprocess: bool) -> None:
        if self._checksum_lookup is None:
            return
        existing_id = await self._checksum_lookup(checksum)
        if existing_id and not allow_reprocess:
            raise DuplicateFileError(existing_document_id=str(existing_id))

    def _detect_mime(self, content: bytes) -> str:
        """Detect MIME type from magic bytes (not Content-Type header).
        Falls back to header-byte detection if libmagic is unavailable."""
        if _MAGIC_AVAILABLE and _magic is not None:
            try:
                mime = _magic.from_buffer(content[:4096], mime=True)
                return mime
            except Exception as e:
                logger.warning("MIME detection (magic) failed", error=str(e))

        # ── Fallback: file signature (magic bytes) detection ──────────────────
        return _detect_mime_from_headers(content)


    def _check_mime_vs_extension(self, detected_mime: str, filename: str) -> None:
        """
        Verify that the detected MIME type matches the claimed extension.
        Catches renamed files (e.g., malicious.exe → invoice.pdf).
        """
        ext = Path(filename).suffix.lower()

        if detected_mime not in ALLOWED_MIMES:
            raise UnsupportedFormatError(
                f"Detected file type '{detected_mime}' is not supported."
            )

        expected_ext = ALLOWED_MIMES.get(detected_mime, "")
        # Allow .csv for text/plain (common for CSV files)
        if ext == ".csv" and detected_mime == "text/plain":
            return
        if ext != expected_ext and expected_ext:
            raise FileMimeTypeMismatchError(
                f"File claims to be '{ext}' but content is '{detected_mime}' ({expected_ext}). "
                "Possible file rename or corruption."
            )

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 3 — Content Validation: PDF
    # ══════════════════════════════════════════════════════════════════════════

    async def _validate_pdf(
        self,
        content: bytes,
        checksum: str,
        detected_mime: str,
        safe_filename: str,
        original_filename: str,
        pdf_password: str | None,
    ) -> ValidatedFile:
        pdf_is_encrypted = False
        pdf_encryption_type = None
        pdf_password_used = False
        ocr_needed = False
        scanned_pages: list[int] = []

        try:
            reader = PdfReader(io.BytesIO(content))
        except (PdfReadError, PdfStreamError, Exception) as e:
            raise PDFCorruptedError(
                f"Could not open PDF — file may be corrupted or truncated. Detail: {e}"
            )

        # ── Encryption check ──────────────────────────────────────────────────
        if reader.is_encrypted:
            pdf_is_encrypted = True
            pdf_encryption_type = self._detect_pdf_encryption(reader)

            # Try empty password first (some PDFs "encrypted" with no password)
            try:
                result = reader.decrypt("")
                if result == 0:
                    # Empty password didn't work — need real password
                    if not pdf_password:
                        raise PDFPasswordRequiredError()

                    result = reader.decrypt(pdf_password)
                    if result == 0:
                        raise PDFWrongPasswordError(
                            "The provided PDF password is incorrect."
                        )
                    pdf_password_used = True
                    logger.info("PDF decrypted successfully with provided password")
            except (PDFPasswordRequiredError, PDFWrongPasswordError):
                raise
            except Exception as e:
                if not pdf_password:
                    raise PDFPasswordRequiredError()
                raise PDFWrongPasswordError(f"PDF decryption failed: {e}")

        # ── Page count check ──────────────────────────────────────────────────
        try:
            page_count = len(reader.pages)
        except Exception:
            page_count = 0

        if page_count > settings.max_pdf_pages:
            raise PDFTooManyPagesError(
                f"PDF has {page_count} pages, exceeding the limit of {settings.max_pdf_pages}.",
                detail={"page_count": page_count, "max_pages": settings.max_pdf_pages},
            )

        # ── Scanned page detection ────────────────────────────────────────────
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
                if len(text.strip()) < settings.pdf_min_text_per_page:
                    scanned_pages.append(i)
            except Exception:
                scanned_pages.append(i)  # If we can't extract text, assume scanned

        if scanned_pages:
            ocr_needed = True
            logger.info(
                "Scanned pages detected — OCR will be required",
                scanned_pages=scanned_pages,
            )

        logger.info(
            "PDF validated",
            pages=page_count,
            encrypted=pdf_is_encrypted,
            ocr_needed=ocr_needed,
        )

        return ValidatedFile(
            content=content,
            checksum_sha256=checksum,
            detected_mime=detected_mime,
            safe_filename=safe_filename,
            original_filename=original_filename,
            file_size_bytes=len(content),
            file_extension=".pdf",
            pdf_page_count=page_count,
            pdf_is_encrypted=pdf_is_encrypted,
            pdf_encryption_type=pdf_encryption_type,
            pdf_password_used=pdf_password_used,
            ocr_needed=ocr_needed,
            scanned_pages=scanned_pages if scanned_pages else None,
        )

    def _detect_pdf_encryption(self, reader: PdfReader) -> str | None:
        """Best-effort detection of PDF encryption algorithm."""
        try:
            encrypt_dict = reader.trailer.get("/Encrypt", {})
            if hasattr(encrypt_dict, "get"):
                v = encrypt_dict.get("/V", 0)
                return {1: "RC4-40", 2: "RC4-128", 4: "AES-128", 5: "AES-256"}.get(v, f"V={v}")
        except Exception:
            pass
        return "unknown"

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 3 — Content Validation: CSV
    # ══════════════════════════════════════════════════════════════════════════

    def _validate_csv(
        self,
        content: bytes,
        checksum: str,
        detected_mime: str,
        safe_filename: str,
        original_filename: str,
    ) -> ValidatedFile:
        """Quick CSV sanity check (full parsing happens in the worker)."""
        if len(content) < 10:
            raise CSVParseError("CSV file is too small to contain valid data.")

        # Detect encoding hint for the parser
        import chardet
        detected = chardet.detect(content[:2048])
        encoding_hint = detected.get("encoding", "utf-8")

        return ValidatedFile(
            content=content,
            checksum_sha256=checksum,
            detected_mime=detected_mime,
            safe_filename=safe_filename,
            original_filename=original_filename,
            file_size_bytes=len(content),
            file_extension=".csv",
            csv_encoding_hint=encoding_hint,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 3 — Content Validation: Excel → CSV conversion
    # ══════════════════════════════════════════════════════════════════════════

    def _validate_excel(
        self,
        content: bytes,
        checksum: str,
        safe_filename: str,
        original_filename: str,
    ) -> ValidatedFile:
        """
        Auto-convert Excel files to CSV in-memory.
        Returns a ValidatedFile with file_extension='.csv' and converted content.
        """
        import openpyxl
        import io as _io
        import csv as _csv

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            output = _io.StringIO()
            writer = _csv.writer(output)
            for row in ws.iter_rows(values_only=True):
                writer.writerow([str(v) if v is not None else "" for v in row])
            wb.close()
            csv_content = output.getvalue().encode("utf-8")
        except Exception as e:
            raise CSVParseError(f"Could not convert Excel file to CSV: {e}")

        logger.info("Excel file auto-converted to CSV", original=original_filename)

        return ValidatedFile(
            content=csv_content,
            checksum_sha256=checksum,            # Checksum of original Excel file
            detected_mime="application/vnd.ms-excel",
            safe_filename=safe_filename.replace(".xlsx", ".csv"),
            original_filename=original_filename,
            file_size_bytes=len(content),
            file_extension=".csv",
            csv_is_excel=True,
        )


# ── Module-level helpers ───────────────────────────────────────────────────────

def _detect_mime_from_headers(content: bytes) -> str:
    """
    Pure-Python MIME detection from file magic bytes.
    Used as fallback when python-magic / libmagic is unavailable (e.g. Windows).
    """
    if content[:4] == b"%PDF":
        return "application/pdf"
    # ZIP-based formats (XLSX, DOCX, etc.)
    if content[:2] == b"PK":
        # Peek inside for XLSX marker
        if b"xl/" in content[:2000] or b"[Content_Types]" in content[:2000]:
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return "application/zip"
    # UTF-16 BOM (some CSV exports)
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "text/plain"
    # UTF-8 BOM
    if content[:3] == b"\xef\xbb\xbf":
        return "text/plain"
    # Try to decode as text — if it succeeds, likely CSV
    try:
        sample = content[:1024].decode("utf-8", errors="strict")
        if "\n" in sample or "," in sample or ";" in sample:
            return "text/plain"
    except UnicodeDecodeError:
        pass
    return "application/octet-stream"
