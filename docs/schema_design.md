# Database Schema Design — FINALIZED
## Invoice & Bank Statement Parsing API

---

## ✅ Design Decisions (Resolved)

| Question | Decision |
|---|---|
| **Multi-tenancy** | Single-org internal tool — no tenant isolation needed |
| **Duplicate file upload** | Default: `HTTP 409 Conflict` with clear error. Optional `?allow_reprocess=true` query param to re-run parsing on same file |
| **Vendor deduplication** | Exact case-insensitive match on `canonical_name`. `TODO: add fuzzy/phonetic matching via pg_trgm` |
| **Currency normalization** | Store original currency only. `TODO: add base_currency + exchange_rate columns when FX rates stabilize` |

---

## 1. Domain Entity Map

```
                          ┌──────────────┐
                          │   documents  │  ← raw file artifact (immutable)
                          └──────┬───────┘
                                 │ 1:N  (N because allow_reprocess=true allows new jobs)
                          ┌──────▼───────┐
                          │ processing_  │  ← async job lifecycle
                          │    jobs      │
                          └──────┬───────┘
               ┌─────────────────┴─────────────────┐
               │ (if PDF invoice)                  │ (if CSV bank statement)
        ┌──────▼──────┐                   ┌────────▼──────────┐
        │  invoices   │                   │  bank_statements  │
        └──────┬──────┘                   └────────┬──────────┘
               │ 1:N                               │ 1:N
        ┌──────▼──────┐                   ┌────────▼──────────┐
        │ line_items  │                   │ bank_transactions │
        └──────┬──────┘                   └───────────────────┘
               │N:1
        ┌──────▼──────┐
        │   vendors   │  ← normalized, exact-match deduplicated
        └─────────────┘
```

> **Note on 1:N for processing_jobs**: A document normally has exactly 1 job. When `allow_reprocess=true`, a new job row is created — the old one is kept for audit. The "active" job is always the latest by `created_at`.

---

## 2. Complete Schema DDL

---

### Extensions

```sql
-- UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- TODO: Enable for fuzzy vendor matching in the future
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

---

### ENUM Types

```sql
CREATE TYPE job_status AS ENUM (
    'pending',      -- uploaded, not yet picked up by worker
    'processing',   -- worker actively parsing
    'completed',    -- fully parsed, all fields extracted
    'failed',       -- parsing failed entirely
    'partial'       -- parsed with warnings: missing fields, guessed values
);

CREATE TYPE transaction_type AS ENUM (
    'credit',
    'debit',
    'transfer',
    'fee',
    'interest',
    'unknown'       -- default when type cannot be inferred from description
);

CREATE TYPE document_type AS ENUM (
    'invoice',          -- PDF invoice
    'bank_statement'    -- CSV bank statement
);
```

---

### `documents` — Immutable File Registry

```sql
CREATE TABLE documents (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    filename        VARCHAR(512) NOT NULL,               -- sanitized storage filename
    original_name   VARCHAR(512) NOT NULL,               -- user's original filename
    document_type   document_type NOT NULL,              -- 'invoice' | 'bank_statement'
    file_type       VARCHAR(10)  NOT NULL,               -- 'pdf' | 'csv'
    file_size_bytes BIGINT       NOT NULL,
    checksum_sha256 VARCHAR(64)  NOT NULL UNIQUE,        -- PRIMARY dedup key
    storage_path    TEXT         NOT NULL,               -- S3 key or local path (env-configured)
    uploaded_by     VARCHAR(255) NOT NULL DEFAULT 'system',  -- user/api-key identifier
    uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted      BOOLEAN      NOT NULL DEFAULT FALSE, -- soft delete
    deleted_at      TIMESTAMPTZ,

    CONSTRAINT chk_deleted_at CHECK (
        (is_deleted = FALSE AND deleted_at IS NULL) OR
        (is_deleted = TRUE  AND deleted_at IS NOT NULL)
    )
);

CREATE INDEX idx_documents_checksum      ON documents(checksum_sha256);
CREATE INDEX idx_documents_document_type ON documents(document_type);
CREATE INDEX idx_documents_uploaded_at   ON documents(uploaded_at DESC);
CREATE INDEX idx_documents_is_deleted    ON documents(is_deleted) WHERE is_deleted = FALSE;
```

---

### `processing_jobs` — Async Parsing Lifecycle

```sql
CREATE TABLE processing_jobs (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID         NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status          job_status   NOT NULL DEFAULT 'pending',
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error_message   TEXT,                    -- human-readable failure reason
    error_detail    JSONB,                   -- structured: { "stage": "date_parse", "raw": "...", "traceback": "..." }
    retry_count     SMALLINT     NOT NULL DEFAULT 0,
    max_retries     SMALLINT     NOT NULL DEFAULT 3,
    warnings        JSONB,                   -- non-fatal: [ { "field": "currency", "message": "defaulted to USD" } ]
    parser_version  VARCHAR(50),             -- e.g., "invoice-parser-v1.2.0"
    is_reprocess    BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE when triggered by allow_reprocess=true
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_processing_jobs_document_id ON processing_jobs(document_id);
CREATE INDEX idx_processing_jobs_status      ON processing_jobs(status);
CREATE INDEX idx_processing_jobs_created_at  ON processing_jobs(created_at DESC);

CREATE INDEX idx_processing_jobs_pending
    ON processing_jobs(created_at ASC)
    WHERE status = 'pending';
```

---

### `vendors` — Normalized Vendor Registry

```sql
CREATE TABLE vendors (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  VARCHAR(500) NOT NULL,    -- cleaned, normalized (UPPER TRIM)
    raw_names       TEXT[]       NOT NULL DEFAULT '{}',  -- all seen raw variations
    country         VARCHAR(100),
    tax_id          VARCHAR(100),             -- GST/VAT/EIN if extractable
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_vendors_canonical_name ON vendors(LOWER(canonical_name));
CREATE INDEX idx_vendors_raw_names ON vendors USING GIN(raw_names);
```

---

### `invoices` — Extracted Invoice Data

```sql
CREATE TABLE invoices (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID          NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    processing_job_id   UUID          NOT NULL REFERENCES processing_jobs(id),
    vendor_id           UUID          REFERENCES vendors(id),          -- nullable: match may fail
    invoice_number      VARCHAR(255),
    invoice_date        DATE,                                           -- normalized ISO date
    due_date            DATE,
    currency            CHAR(3),                                        -- ISO 4217
    subtotal            NUMERIC(18, 4),
    tax_amount          NUMERIC(18, 4),
    discount_amount     NUMERIC(18, 4),
    total_amount        NUMERIC(18, 4),

    -- Raw extracted text (preserved for debugging & re-parsing)
    raw_vendor_name     VARCHAR(500),
    raw_date_text       VARCHAR(100),
    raw_total_text      VARCHAR(100),

    -- Parser metadata
    confidence          NUMERIC(4, 3) CHECK (confidence BETWEEN 0 AND 1),  -- 0.000–1.000
    notes               TEXT,

    extracted_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_invoices_document_id    ON invoices(document_id);
CREATE INDEX idx_invoices_vendor_id      ON invoices(vendor_id);
CREATE INDEX idx_invoices_invoice_date   ON invoices(invoice_date);
CREATE INDEX idx_invoices_currency       ON invoices(currency);
CREATE INDEX idx_invoices_total_amount   ON invoices(total_amount);
CREATE INDEX idx_invoices_vendor_date    ON invoices(vendor_id, invoice_date DESC);
```

---

### `invoice_line_items` — Invoice Line Items

```sql
CREATE TABLE invoice_line_items (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID          NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    line_number     SMALLINT      NOT NULL,           -- ordering within invoice
    description     TEXT,
    quantity        NUMERIC(12, 4),
    unit_price      NUMERIC(18, 4),
    line_total      NUMERIC(18, 4),
    tax_rate        NUMERIC(6, 4),                   -- e.g., 0.1800 = 18%
    tax_amount      NUMERIC(18, 4),
    sku             VARCHAR(255),
    unit_of_measure VARCHAR(100),                    -- "hrs", "units", "kg"
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_line_item_order UNIQUE (invoice_id, line_number)
);

CREATE INDEX idx_line_items_invoice_id ON invoice_line_items(invoice_id);
```

---

### `bank_statements` — Statement-Level Metadata

```sql
CREATE TABLE bank_statements (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID          NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    processing_job_id   UUID          NOT NULL REFERENCES processing_jobs(id),
    bank_name           VARCHAR(255),
    account_number      VARCHAR(20),                 -- MASKED: text field for masked values
    account_holder      VARCHAR(500),
    currency            CHAR(3),                     -- ISO 4217
    statement_from      DATE,
    statement_to        DATE,
    opening_balance     NUMERIC(18, 4),
    closing_balance     NUMERIC(18, 4),
    raw_headers         JSON,                        -- original CSV column names as-is
    detected_format     VARCHAR(100),                -- e.g., "HDFC_V2", "ICICI_STANDARD", "GENERIC"

    extracted_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_bank_statements_document_id  ON bank_statements(document_id);
CREATE INDEX idx_bank_statements_currency     ON bank_statements(currency);
CREATE INDEX idx_bank_statements_period       ON bank_statements(statement_from, statement_to);
```

---

### `bank_transactions` — Individual Transaction Rows

```sql
CREATE TABLE bank_transactions (
    id                  UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_statement_id   UUID              NOT NULL REFERENCES bank_statements(id) ON DELETE CASCADE,
    transaction_date    DATE              NOT NULL,
    value_date          DATE,                            -- settlement date if different
    description         TEXT,                            -- cleaned description
    raw_description     TEXT,                            -- original CSV text
    reference_number    VARCHAR(255),                    -- bank's own txn reference
    transaction_type    transaction_type  NOT NULL DEFAULT 'unknown',
    amount              NUMERIC(18, 4)    NOT NULL CHECK (amount >= 0),  -- always positive
    direction           CHAR(1)           NOT NULL CHECK (direction IN ('C', 'D')),
    balance_after       NUMERIC(18, 4),                 -- running balance if present in CSV
    currency            CHAR(3),                         -- per-row if multi-currency statement
    row_index           INTEGER           NOT NULL,      -- original CSV row number (for debugging)
    created_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_transaction_row UNIQUE (bank_statement_id, row_index)
);

CREATE INDEX idx_bank_tx_statement_id   ON bank_transactions(bank_statement_id);
CREATE INDEX idx_bank_tx_date           ON bank_transactions(transaction_date);
CREATE INDEX idx_bank_tx_amount         ON bank_transactions(amount);
CREATE INDEX idx_bank_tx_type           ON bank_transactions(transaction_type);
CREATE INDEX idx_bank_tx_direction      ON bank_transactions(direction);
CREATE INDEX idx_bank_tx_statement_date ON bank_transactions(bank_statement_id, transaction_date DESC);

ALTER TABLE bank_transactions
    ADD COLUMN description_tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', COALESCE(description, ''))) STORED;

CREATE INDEX idx_bank_tx_fts ON bank_transactions USING GIN(description_tsv);
```

---

### `document_tags` — Flexible Key-Value Metadata

```sql
CREATE TABLE document_tags (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID         NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key         VARCHAR(100) NOT NULL,
    value       TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_document_tag UNIQUE (document_id, key)
);

CREATE INDEX idx_document_tags_document_id ON document_tags(document_id);
```
