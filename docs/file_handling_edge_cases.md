# File Handling — Edge Cases & Design
## Invoice & Bank Statement Parsing API

---

## Overview: 3-Stage Validation Pipeline

Every file goes through three stages **before** a worker ever touches it for parsing.
Fail fast, fail clearly.

```
Stage 1: HTTP Layer          Stage 2: File Integrity        Stage 3: Content Validation
─────────────────────        ──────────────────────         ───────────────────────────
Size limit check        →    Magic bytes / MIME check  →    PDF: password? scanned?
Extension whitelist          Checksum (dedup)               CSV: encoding? delimiter?
Filename sanitization        Zero-byte check                Both: corrupt / unreadable?
Content-Type header          Malware sig (optional)
                                    ↓
                            PASS → enqueue processing_job
                            FAIL → HTTP error, no DB row created (except document row for audit)
```

---

## PDF Edge Cases

---

### 1. Password-Protected PDF

**Detection:** Attempt to open with `pypdf` or `pdfplumber` — if it raises `PasswordError`, it's encrypted.

**Sub-cases:**
- `encrypted = True, decrypt('')` succeeds → **empty password** (open password). Treat as normal, log warning.
- `encrypted = True, decrypt(password)` with user-supplied password → needs password in request
- Encrypted with owner password only (print/edit locked but readable) → extract normally
- Unknown/no password available → cannot parse, fail gracefully

**API Design:**
```
POST /api/v1/documents/upload
Content-Type: multipart/form-data

Fields:
  - file: <the PDF>
  - pdf_password: "optional_password"   ← new optional field
```

**Response flow:**
```
┌─ No password provided, PDF is encrypted
│     → HTTP 422 Unprocessable Entity
│       { "error": "PDF_PASSWORD_REQUIRED",
│         "message": "This PDF is password-protected. Re-upload with pdf_password field.",
│         "document_id": "<uuid>"  ← document is stored, job status = 'failed'
│       }
│
├─ Password provided but wrong
│     → HTTP 422
│       { "error": "PDF_WRONG_PASSWORD",
│         "message": "The provided password did not unlock this PDF." }
│
└─ Password correct
      → proceed normally, log: password_used=True in processing_jobs.warnings
```

**Schema addition on `processing_jobs`:**
```sql
pdf_password_used   BOOLEAN  NOT NULL DEFAULT FALSE,  -- was a password needed?
pdf_encryption_type VARCHAR(50),                       -- e.g., 'AES-256', 'RC4-128'
```

> **Security note:** The password must NEVER be stored in DB or logs. Use it in-memory, discard immediately after decryption. Pass it as a runtime parameter to the worker only.

---

### 2. Scanned PDF (Image-Only — No Text Layer)

The most common real-world problem. A PDF that is just photographs of pages.

**Detection:**
```python
# After extracting text with pdfplumber:
text = page.extract_text()
if not text or len(text.strip()) < MIN_TEXT_THRESHOLD:  # e.g., < 50 chars
    # likely a scanned page — trigger OCR
```

**Sub-cases:**
| Scenario | Handling |
|---|---|
| All pages scanned | Full OCR via `pytesseract` or cloud Vision API |
| Mixed: some text pages, some scanned | Per-page detection; hybrid extraction |
| Scanned but rotated pages | Deskew before OCR (`cv2.getRotationMatrix2D`) |
| Low-resolution scan (< 150 DPI) | OCR will be poor; set low `confidence` score, add warning |
| Scanned with watermarks/stamps | Degrade confidence score, include in warnings |

**Processing job columns to add:**
```sql
ocr_used            BOOLEAN   NOT NULL DEFAULT FALSE,
ocr_engine          VARCHAR(50),      -- 'tesseract-v5', 'google-vision', 'aws-textract'
ocr_confidence_avg  NUMERIC(4,3),     -- avg confidence across all pages
scanned_pages       INTEGER[],        -- which page numbers needed OCR: [1, 3, 4]
```

**Response (async):** OCR takes longer. Return `202 Accepted` with `job_id`. Client polls `/jobs/{id}`.

---

### 3. Corrupted / Malformed PDF

**Scenarios:**
- PDF header missing (`%PDF-` magic bytes not found) → Stage 2 catches this
- Truncated file (upload interrupted) → `pypdf` raises `PdfReadError`
- Valid PDF header but corrupted internal structure → partial extraction possible
- PDF spec version too new for parser → log parser version mismatch

**Handling:**
```python
try:
    reader = PdfReader(file_bytes)
except PdfReadError as e:
    # Update job: status='failed', error_detail={'stage': 'pdf_open', 'error': str(e)}
    raise ParsingError("PDF_CORRUPTED", "File appears corrupted or incomplete")
```

**HTTP Response:**
```json
{ "error": "PDF_CORRUPTED",
  "message": "The PDF file appears to be corrupted or incomplete.",
  "document_id": "<uuid>",
  "hint": "Try re-downloading the original file and re-uploading." }
```

---

### 4. Multi-Invoice PDF

One PDF file containing multiple invoices (e.g., a monthly invoice batch from a vendor).

**Detection heuristics:**
- Multiple `Invoice #` patterns found
- Page count > 1 with repeating header patterns
- Total amount mismatch (sum of line items ≠ grand total at bottom)

**Handling Strategy (chosen approach):**
- Create **one `document` row** (single upload)
- Create **multiple `invoice` rows** linked to same `document_id`
- Each invoice gets its own `page_range` metadata

**Schema addition on `invoices`:**
```sql
page_range_start    SMALLINT,   -- first PDF page of this invoice (1-indexed)
page_range_end      SMALLINT,   -- last PDF page of this invoice
invoice_index       SMALLINT NOT NULL DEFAULT 0,  -- 0 for first/only invoice
```

---

### 5. PDF With Form Fields (AcroForms)

Some invoices are interactive PDFs with fillable form fields.

**Handling:** `pypdf` can extract field values directly — often more reliable than text extraction.
```python
fields = reader.get_fields()
# Try form fields first; fall back to text extraction
```

---

### 6. Non-PDF Disguised as PDF (MIME Spoofing)

User uploads `malicious.exe` renamed to `invoice.pdf`.

**Detection (Stage 2 — Magic Bytes):**
```python
import magic
mime = magic.from_buffer(file_bytes[:2048], mime=True)
if mime != 'application/pdf':
    raise ValidationError("FILE_TYPE_MISMATCH",
        f"File claims to be PDF but is detected as {mime}")
```

**HTTP 415 Unsupported Media Type**

---

### 7. Very Large PDF

**Limits (configurable via env vars):**
```env
MAX_PDF_SIZE_MB=50
MAX_PDF_PAGES=200
```

**Handling:**
- Size check at upload time (before saving to disk) → `HTTP 413 Content Too Large`
- Page count check after opening (before extraction) → fail with `PDF_TOO_MANY_PAGES`

---

### 8. Empty / Zero-Byte PDF

**Detection:** `file_size_bytes == 0` at upload time. Reject before saving.
```json
{ "error": "EMPTY_FILE", "message": "The uploaded file is empty (0 bytes)." }
```
**HTTP 400 Bad Request**

---

### 9. PDFs With Special Characters / Non-Latin Text

Invoices from international vendors may contain Arabic, Chinese, Hindi text.

**Handling:**
- Extract as Unicode — `pdfplumber` handles this well
- Set `confidence` lower if non-Latin characters dominate and OCR fallback isn't multilingual
- Store `raw_vendor_name` as-is (UTF-8)

---

## CSV Edge Cases

---

### 1. Wrong Delimiter

Banks export CSVs with different delimiters: `,` `;` `\t` `|`

**Auto-detection:**
```python
import csv
dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
# If Sniffer fails → try each delimiter, pick the one yielding most consistent column counts
```

**Schema addition on `bank_statements`:**
```sql
detected_delimiter  CHAR(1),    -- ',' | ';' | '\t' | '|'
detected_encoding   VARCHAR(30), -- 'utf-8' | 'latin-1' | 'utf-16' | 'windows-1252'
```

---

### 2. Wrong / Mixed Encoding

**Detection & handling:**
```python
import chardet
raw = file.read()
result = chardet.detect(raw)
encoding = result['encoding']  -- 'UTF-8', 'ISO-8859-1', 'Windows-1252'
text = raw.decode(encoding, errors='replace')  -- 'replace' prevents hard crash
# Log any replacement characters as a warning
```

**BOM (Byte Order Mark):** Some Excel-exported CSVs have UTF-8-BOM. Use `utf-8-sig` encoding to strip it automatically.

---

### 3. Non-Standard Amount Formats

| Format | Example | Region |
|---|---|---|
| Standard | `1234.56` | US/UK |
| European | `1.234,56` | Germany, France |
| Indian | `1,23,456.78` | India |
| Parentheses negative | `(1234.56)` | Accounting software |
| Suffix negative | `1234.56 DR` | Some banks |
| With currency symbol | `$1,234.56` | Mixed |
| Blank / dash | `-` or `` | Missing value |

**Normalization function:**
```python
def parse_amount(raw: str) -> tuple[Decimal, str | None]:
    """
    Returns (amount, warning_message)
    """
    s = raw.strip()
    if not s or s in ('-', 'N/A', 'nil'):
        return None, "Amount missing or null"

    is_negative = s.startswith('(') and s.endswith(')')
    s = s.strip('()').replace('DR', '').replace('CR', '').strip()
    # Remove currency symbols
    s = re.sub(r'[£$€₹¥]', '', s)
    # Detect European format: last separator is comma with 2 digits after
    if re.search(r',\d{2}$', s):
        s = s.replace('.', '').replace(',', '.')
    else:
        s = s.replace(',', '')
    return Decimal(s), None
```

---

### 4. Non-Standard Date Formats

| Format | Example |
|---|---|
| ISO | `2026-05-29` |
| UK | `29/05/2026` |
| US | `05/29/2026` |
| Short year | `29/05/26` |
| Month name | `29-May-2026`, `May 29, 2026` |
| Excel serial | `46044` (days since 1900-01-01) |
| Timestamp | `2026-05-29 14:30:00` |
| Ambiguous | `04/05/2026` → April 5 or May 4? |

**Strategy:**
```python
from dateutil import parser as dateparser

def parse_date(raw: str, dayfirst_hint: bool = True) -> tuple[date, str | None]:
    # Try ISO first (unambiguous)
    # Try dateutil with dayfirst=True (most bank statements are non-US)
    # If ambiguous, log warning with original value
    # If Excel serial number detected, convert from Excel epoch
```

---

### 5. Missing or Extra Columns

**Scenarios:**
| Case | Handling |
|---|---|
| Required column missing | Mark job `partial`, add to `warnings`, set field to `NULL` |
| Extra/unknown columns | Silently ignore, store column names in `raw_headers` JSONB |
| Columns in wrong order | Always map by header name, never by position |
| No header row | Attempt positional heuristics, set confidence=0.3, warn |
| Duplicate header names | Deduplicate by appending `_2`, `_3` suffix |

---

### 6. Malformed Rows

**Scenarios:**
- Row has fewer columns than header → skip with warning (include `row_index`)
- Row has more columns than header → truncate extra, warn
- Row is entirely blank → skip silently
- Summary/total row at bottom (e.g., "Total", "Balance") → detect and skip (don't parse as transaction)
- Header row appears mid-file (continuation page) → detect and skip

**Detection for summary rows:**
```python
SKIP_PATTERNS = ['total', 'balance', 'closing', 'opening', 'subtotal', 'grand total']
if any(p in row[0].lower() for p in SKIP_PATTERNS):
    continue  # skip this row
```

---

### 7. Very Large CSV

**Limits (env var):**
```env
MAX_CSV_SIZE_MB=25
MAX_CSV_ROWS=100000
```

**Streaming parse:** Never load the whole file into memory. Use chunked reading:
```python
for chunk in pd.read_csv(file, chunksize=1000):
    process_chunk(chunk)
```

---

### 8. Multi-Currency CSV

Some statements have a `Currency` column that changes per row.

**Handling:**
- Store `currency` at the `bank_transactions` level (not just statement level)
- If all rows have same currency → also populate `bank_statements.currency`
- If mixed → leave `bank_statements.currency = NULL`, flag in warnings

---

### 9. Excel File Uploaded as CSV

User uploads `.xlsx` renamed to `.csv` (or uploads an `.xlsx` directly).

**Detection:**
```python
mime = magic.from_buffer(file_bytes[:2048], mime=True)
# 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
if mime in EXCEL_MIMES:
    # Auto-convert using openpyxl, then process as CSV
    # OR reject with helpful message
```

**Decision: Auto-convert** (better UX for internal tool).

---

## General Upload Edge Cases

---

### 10. Concurrent Duplicate Upload (Race Condition)

Two requests upload the same file at the same millisecond.

**Problem:** Both pass the checksum check before either inserts the row.

**Fix:** Database-level unique constraint on `checksum_sha256` catches this:
```sql
-- If second INSERT fails with UniqueViolation:
except UniqueViolation:
    raise HTTP409("DUPLICATE_FILE", existing_doc_id=lookup_by_checksum())
```
Use `INSERT ... ON CONFLICT DO NOTHING RETURNING id` pattern.

---

### 11. Path Traversal in Filename

User uploads file named `../../etc/passwd.pdf`.

**Fix (always apply):**
```python
from pathlib import Path
safe_name = Path(original_filename).name  # strips directory components
safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', safe_name)  # whitelist chars only
```

---

### 12. Upload Interrupted Mid-Stream

Network drops after 50% of file is sent.

**Handling:**
- FastAPI will raise an exception during `await file.read()`
- No `documents` row created (validate fully before DB write)
- Return `HTTP 400` or `499` (client closed request)

---

### 13. Wrong Content-Type Header

Client sends `Content-Type: text/plain` but file is a PDF.

**Policy:** Trust the file content (magic bytes), not the Content-Type header. Log the mismatch as a warning.

---

## Security Notes

1. **PDF password** — passed as form field, used in-memory only, **never logged, never stored**
2. **Filename sanitization** — always done before any filesystem operation
3. **Magic byte check** — always done; Content-Type header is untrusted
4. **File size limit** — enforced at streaming level, before full read into memory
5. **OCR output** — treated as untrusted text (same input validation as direct text)
