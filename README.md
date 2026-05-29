# FinParse API 🏦

> **Production-grade FastAPI backend for parsing Invoice (PDF) and Bank Statement (CSV) files into structured financial data.**

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Project Documentation](#-project-documentation)
- [Architecture](#architecture)
- [Database Schema](#database-schema)
- [CSV Parser Pipeline](#csv-parser-pipeline)
- [File Handling & Edge Cases](#file-handling--edge-cases)
- [Quick Start](#quick-start)
- [API Documentation](#api-documentation)
- [Running Tests](#running-tests)
- [Deployment](#deployment)
- [Known Limitations](#known-limitations)


---

## Overview

FinParse is an internal tool for processing financial documents from multiple vendors and banks. It:

- Accepts **PDF invoices** and **CSV bank statements** (+ XLSX auto-converted)
- Runs a **5-stage parsing pipeline** that handles inconsistent real-world formats
- Persists structured data in **PostgreSQL** with normalized schema
- Exposes **RESTful APIs** for upload, retrieval, filtering, and deletion
- Handles **14 categories of file errors** gracefully with structured error responses

---

## 📁 Project Documentation

Detailed architecture, design decisions, and testing logs are located in the `docs/` folder:

* **[Database Design & ER Diagram](docs/db_design_and_er.md)**: Explains the relational database table design, money safety rules, constraints, and holds the visual Mermaid Entity-Relationship (ER) diagram.
* **[Database Schema DDL Specification](docs/schema_design.md)**: Contains the exact SQL DDL specs, indices, and database decisions.
* **[File Handling & Edge Cases](docs/file_handling_edge_cases.md)**: Technical breakdown of the 3-stage validation pipeline and how PDF/CSV edge cases are resolved.
* **[Manual Testing Log](docs/manual_testing_log.md)**: Structured template to document your manual testing steps via Swagger UI and attach validation screenshots.


---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          FastAPI (async)                            │
│  POST /api/v1/documents/upload  →  GET /api/v1/documents/{id}      │
└─────────────┬───────────────────────────────────────────────────────┘
              │
     ┌────────▼─────────┐
     │  FileValidator   │   ← 3-stage: extension → MIME → content
     │  (3-stage)       │
     └────────┬─────────┘
              │
      ┌───────▼────────┐        ┌─────────────────────┐
      │  CSV Parser    │        │  PDF Validator       │
      │  (5-stage)     │        │  (validations only)  │
      └───────┬────────┘        └──────────────────────┘
              │
   ┌──────────▼──────────┐
   │  DocumentService    │   ← orchestrates DB writes
   └──────────┬──────────┘
              │
   ┌──────────▼──────────┐
   │  PostgreSQL          │   ← via SQLAlchemy (async)
   └─────────────────────┘
```

### Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI 0.111 (async) |
| ORM | SQLAlchemy 2.0 (async) |
| Database | PostgreSQL 16 |
| Background Jobs | Celery + Redis (wired, optional) |
| Logging | structlog (JSON in prod, pretty in dev) |
| Containerization | Docker Compose |

---

## Database Schema

### Entity Overview

```
documents (1) ──────────────── (N) processing_jobs
    │
    ├── (1) invoices
    │         └── (N) line_items
    │         └── (N:1) vendors
    │
    ├── (1) bank_statements
    │         └── (N) bank_transactions
    │
    └── (N) document_tags
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| `documents` is immutable | Raw file artifact never mutated — source of truth |
| `processing_jobs` separate from `documents` | Supports re-parsing without data loss |
| `amount >= 0` + explicit `direction ('C'/'D')` | No sign bugs across bank formats |
| `NUMERIC(18,4)` for all money | Never use floats for financial data |
| `checksum_sha256 UNIQUE` | Deduplication at DB level (race-condition safe) |
| `raw_*` columns on every extracted table | Original text preserved for debugging |
| `status = 'partial'` as first-class state | Real-world data is messy — partial success is valid |

See [schema_design.md](docs/schema_design.md) for full DDL.

---

## CSV Parser Pipeline

The CSV parser runs a **5-stage pipeline**, each stage independently testable:

```
Stage 1: FileReader      → Encoding detection (chardet + fallbacks)
Stage 2: FormatDetector  → Delimiter, header row discovery, column mapping
Stage 3: RowFilter       → Skip blanks, metadata rows, summary rows
Stage 4: RowParser       → Parse date + amount + description per row
Stage 5: PostProcessor   → Balance continuity, currency detection, stats
```

### Supported Formats

**Delimiters**: `,` `;` `\t` `|`

**Encodings**: UTF-8, UTF-8-BOM, Latin-1, Windows-1252, UTF-16

**Amount formats**:
| Format | Example |
|---|---|
| Standard | `1,234.56` |
| European | `1.234,56` |
| Indian grouping | `1,23,456.78` |
| Parenthetical negative | `(1,234.56)` |
| DR/CR suffix | `500.00 DR` |
| Currency symbol | `$500`, `₹1,000`, `€250` |

**Date formats**:
| Format | Example |
|---|---|
| ISO | `2026-05-29` |
| UK/EU | `29/05/2026` |
| US | `05/29/2026` |
| Month name | `29-May-2026` |
| Excel serial | `46044` |
| Timestamp | `2026-05-29 14:30:00` |

---

## File Handling & Edge Cases

### PDF Validations
| Case | Handling |
|---|---|
| Password-protected | Prompt for password via `pdf_password` form field; never stored |
| Wrong password | `422 PDF_WRONG_PASSWORD` |
| Scanned (image-only) | OCR detection flag set; `ocr_needed=true` in job |
| Corrupted / truncated | `422 PDF_CORRUPTED` |
| Too many pages | `422 PDF_TOO_MANY_PAGES` (limit: env-configurable) |
| MIME spoofing | Magic byte check, `415 FILE_TYPE_MISMATCH` |

### CSV Edge Cases
| Case | Handling |
|---|---|
| Auto-encoding detection | chardet + fallback chain |
| Mixed delimiters | Sniffer → scoring fallback |
| European decimal comma | Regex detection, auto-normalize |
| Metadata rows before header | Header row discovery (first 15 rows scanned) |
| Summary rows (Total, Balance) | Regex pattern skip |
| XLSX uploaded as CSV | Auto-converted via openpyxl |
| Max rows exceeded | Truncate + warning in job |

### Error Code Reference
| Code | HTTP | Trigger |
|---|---|---|
| `DUPLICATE_FILE` | 409 | Same SHA-256, use `?allow_reprocess=true` to re-parse |
| `PDF_PASSWORD_REQUIRED` | 422 | Encrypted PDF, no password given |
| `PDF_WRONG_PASSWORD` | 422 | Wrong password |
| `PDF_CORRUPTED` | 422 | Malformed/truncated PDF |
| `CSV_MISSING_REQUIRED_COLUMNS` | 422 | No date or amount column found |
| `FILE_TOO_LARGE` | 413 | Exceeds size limit |
| `FILE_TYPE_MISMATCH` | 415 | MIME spoofing detected |
| `PARTIAL_PARSE` | 207 | Parsed with warnings |

---

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- (Optional) PostgreSQL 16 if running without Docker

### 1. Clone and configure

```bash
git clone https://github.com/Darshan-dlr/finparse-api.git
cd finparse-api
cp .env.example .env
# Edit .env with your values
```

### 2. Start with Docker Compose

```bash
# Start PostgreSQL + Redis + API + Worker
docker-compose up --build

# Or just the database (run API locally)
docker-compose up -d db redis
```

### 3. Run locally (without Docker)

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Start the API
uvicorn app.main:app --reload --port 8000
```

### 4. Access Swagger UI

```
http://localhost:8000/docs
```

---

## API Documentation

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/documents/upload` | Upload CSV/PDF for parsing |
| `GET` | `/api/v1/documents/` | List & filter documents |
| `GET` | `/api/v1/documents/{id}` | Get document + parsed data |
| `GET` | `/api/v1/documents/{id}/job` | Get latest job status |
| `DELETE` | `/api/v1/documents/{id}` | Soft-delete document |
| `GET` | `/health` | Health check |

### Upload Example

```bash
# Upload a CSV bank statement
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -F "file=@tests/sample_files/standard_bank_statement.csv"

# Upload with duplicate handling
curl -X POST "http://localhost:8000/api/v1/documents/upload?allow_reprocess=true" \
  -F "file=@tests/sample_files/standard_bank_statement.csv"

# Upload a password-protected PDF
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -F "file=@invoice.pdf" \
  -F "pdf_password=secret123"
```

### Filter Example

```bash
# Filter by document type and status
curl "http://localhost:8000/api/v1/documents/?document_type=bank_statement&status=completed"

# Filter by currency
curl "http://localhost:8000/api/v1/documents/?currency=INR&limit=50"
```

---

## Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=app --cov-report=html

# Run just the CSV parser tests (no DB needed)
pytest tests/test_csv_parser.py -v

# Run specific test class
pytest tests/test_csv_parser.py::TestAmountParser -v
```

### Test Coverage

| Module | Tests |
|---|---|
| `amount_parser.py` | 17 cases — all format variants |
| `date_parser.py` | 13 cases — ISO, UK, US, Excel serial, ambiguous |
| `csv_parser.py` | 13 integration cases — 3 real bank statement formats |

---

## Deployment

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL async URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `STORAGE_BACKEND` | `local` | `local` \| `s3` \| `gcs` |
| `MAX_PDF_SIZE_MB` | `50` | PDF upload limit |
| `MAX_CSV_SIZE_MB` | `25` | CSV upload limit |
| `MAX_PDF_PAGES` | `200` | Max pages per PDF |
| `MAX_CSV_ROWS` | `100000` | Max rows per CSV |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` |
| `ENVIRONMENT` | `development` | `development` \| `production` |

### Cloud Deployment (AWS)

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  API Gateway│────▶│  ECS Fargate │────▶│  RDS Postgres│
│  (or ALB)   │     │  (FastAPI)   │     │  (db.t4g.med)│
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
                    ┌──────▼───────┐     ┌──────────────┐
                    │  Celery      │────▶│  ElastiCache │
                    │  Workers     │     │  (Redis)     │
                    └──────────────┘     └──────────────┘
                           │
                    ┌──────▼───────┐
                    │  S3 Bucket   │  ← file storage
                    └──────────────┘
```

**Quick Render deploy:**
1. Connect GitHub repo to Render
2. Add PostgreSQL service
3. Set environment variables
4. Deploy with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## Project Structure

```
finparse-api/
├── app/
│   ├── main.py                    # FastAPI app + error handlers
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── database.py                # Async SQLAlchemy engine
│   ├── dependencies.py            # FastAPI DI (DB session)
│   ├── core/
│   │   ├── exceptions.py          # 14 typed exception classes
│   │   └── logging.py             # structlog setup
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── document.py
│   │   ├── processing_job.py
│   │   ├── bank_statement.py      # BankStatement + BankTransaction
│   │   └── document_tag.py
│   ├── parsers/
│   │   └── csv_parser.py          # 5-stage CSV pipeline (main focus)
│   ├── utils/
│   │   ├── amount_parser.py       # 9 amount format variants
│   │   └── date_parser.py         # 8 date format variants + Excel
│   ├── validators/
│   │   └── file_validator.py      # 3-stage file validation
│   ├── services/
│   │   └── document_service.py    # Upload + parse + persist
│   └── api/v1/
│       ├── router.py
│       └── endpoints/
│           └── documents.py
├── tests/
│   ├── sample_files/
│   │   ├── standard_bank_statement.csv
│   │   ├── european_semicolon_statement.csv
│   │   └── hdfc_style_statement.csv
│   └── test_csv_parser.py         # 43 test cases
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Known Limitations & Assumptions

1. **Invoice parsing (PDF)** — Fully implemented using a local heuristic and table extraction pipeline (`pdfplumber`). For complex/varying formats in production, see the **Advanced ML/AI Suggestions** below.

2. **OCR for scanned PDFs** — Detection is in place (`ocr_needed` flag), but Tesseract/cloud OCR integration is not yet connected. Scanned-only PDFs will raise an `OCRFailedError`.

3. **Vendor deduplication** — Exact case-insensitive match only. Fuzzy matching via `pg_trgm` or sentence embeddings is marked as TODO.

4. **Cross-currency normalization** — Amounts stored in original currency. Base currency conversion with exchange rates is marked as TODO.

5. **Authentication** — No auth implemented. Placeholder `uploaded_by` field ready for JWT/API key integration.

6. **Background processing** — Celery is wired in `docker-compose.yml` but parsing runs synchronously in the request for now. Move `_parse_csv()` and `_parse_pdf()` to Celery tasks for production.

7. **Storage** — Local disk storage only. S3/GCS integration hooks are in place (swap `_save_to_storage()` in `DocumentService`).

8. **Multi-invoice PDFs** — Schema supports `invoice_index` and `page_range_*` but multi-invoice splitting is not yet implemented in the parser.


## Advanced ML/AI Document Parsing Suggestions

In production systems, rule-based heuristics and standard regex engines can fail if invoices change layout or use complex multi-column structures. Below are recommended modern approaches to achieve near-100% parsing accuracy:

1. **Multimodal LLMs (Recommended)**:
   - Use APIs like **Gemini Flash / Pro** or **GPT-4o** to parse document pages (as images or extracted text layouts).
   - Use Pydantic schemas with LLM Structured Outputs (JSON Schema enforcement) to parse invoice totals, vendor details, and line-item tables with high accuracy, automatically handling diverse formats.

2. **Fine-Tuned Layout/Visual Transformers**:
   - Use open-source transformer models like **LayoutLM** (v1/v2/v3) or **Donut** (OCR-free document understander) to map text and coordinates (bounding boxes) to structured tables and headers.

3. **Document AI SaaS Engines**:
   - Integrate with pre-trained specialized models like **Google Cloud Document AI (Invoice Parser)** or **AWS Textract (Analyze Expense)**. These services automatically resolve fields, line items, tax breakdowns, and currencies with built-in OCR.



