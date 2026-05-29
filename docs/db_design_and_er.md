# Database Design & ER Diagram
## Invoice & Bank Statement Parsing API

This document describes the relational database design for the FastAPI backend, detailing the tables, field types, constraints, and relationships. It includes the complete Mermaid ER diagram at the end.

---

## 1. Domain Entities & Database Tables

The database is built around **8 core tables** designed to handle raw files, parsing jobs, invoices, bank statements, transactions, and tags.

### 1. `documents` (Raw File Registry)
* **Purpose**: Immutable registry of all uploaded files. Every upload creates a new row. Soft-deletes are supported.
* **Primary Key**: `id` (UUID)
* **Keys & Indexes**: 
  * `checksum_sha256` (VARCHAR, Unique Index): Computes SHA-256 to detect duplicate uploads.
* **Consistency Check**: Constraint ensures that if `is_deleted` is true, `deleted_at` is populated, and vice-versa.

### 2. `processing_jobs` (Parsing Lifecycle Tracker)
* **Purpose**: Tracks the asynchronous lifecycle of parsing a document.
* **Status Enum**: `pending` | `processing` | `completed` | `failed` | `partial`
* **Features**:
  * Tracks average OCR confidence, scanned pages, and password protection flags for PDFs.
  * Captures detailed parsing failure call stacks or field warnings in JSON structures.
  * Allows multiple jobs per document if `allow_reprocess=true` is passed.

### 3. `vendors` (Normalized Vendor Registry)
* **Purpose**: Stores deduplicated vendors to prevent redundancy (e.g. mapping "Amazon", "Amazon Inc.", "AMAZON CO" to a single canonical entity).
* **Canonical Matching**: Constraints enforce that `canonical_name` is unique (case-insensitive exact match).
* **Raw Variation Track**: `raw_names` (Text Array) records all raw name variations encountered during parsing for future matching heuristics.

### 4. `invoices` (Extracted Invoice Data)
* **Purpose**: Captures invoice headers, dates, tax amounts, and totals extracted from PDFs.
* **Relations**: Linked to `documents.id` (ON DELETE CASCADE), `processing_jobs.id`, and `vendors.id` (nullable).
* **Parser Metadata**: Contains a `confidence` decimal (0.000 to 1.000) and raw string fields to compare OCR outputs with final parsed values.

### 5. `invoice_line_items` (Invoice Line Items)
* **Purpose**: Breaks down the sub-items billed in each invoice (quantity, unit price, totals, tax, SKU, and unit of measure).
* **Order Constraint**: Unique index on `(invoice_id, line_number)` ensures consistent line ordering.

### 6. `bank_statements` (Statement-Level Metadata)
* **Purpose**: Capture metadata associated with a bank statement file (bank name, account owner, statement period, and opening/closing balances).
* **Security**: `account_number` is stored masked (e.g. `****4321`) to protect client data.
* **Quality Auditing**: Stores delimiter, encoding, format, and raw headers JSON.

### 7. `bank_transactions` (Individual Transactions)
* **Purpose**: Holds parsed transactional ledger rows from CSV statements.
* **Sign Safety**: `amount` is strictly positive (`CHECK (amount >= 0)`) with an explicit `direction` column (`C` = Credit, `D` = Debit).
* **Transaction Types**: Enum values range from `credit`, `debit`, `transfer`, `fee`, `interest`, to `unknown` based on transaction description patterns.


---

## 2. Entity Relationship (ER) Diagram

Below is the Mermaid visual representation of the schemas and foreign key relationships.

```mermaid
erDiagram

    documents {
        UUID id PK
        VARCHAR filename
        VARCHAR original_name
        ENUM document_type
        VARCHAR file_type
        BIGINT file_size_bytes
        VARCHAR checksum_sha256 UK
        TEXT storage_path
        VARCHAR uploaded_by
        TIMESTAMPTZ uploaded_at
        BOOLEAN is_deleted
        TIMESTAMPTZ deleted_at
    }

    processing_jobs {
        UUID id PK
        UUID document_id FK
        ENUM status
        TIMESTAMPTZ started_at
        TIMESTAMPTZ completed_at
        TEXT error_message
        JSON error_detail
        SMALLINT retry_count
        SMALLINT max_retries
        JSON warnings
        VARCHAR parser_version
        BOOLEAN is_reprocess
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    vendors {
        UUID id PK
        VARCHAR canonical_name UK
        TEXT_ARRAY raw_names
        VARCHAR country
        VARCHAR tax_id
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    invoices {
        UUID id PK
        UUID document_id FK
        UUID processing_job_id FK
        UUID vendor_id FK
        VARCHAR invoice_number
        DATE invoice_date
        DATE due_date
        CHAR currency
        NUMERIC subtotal
        NUMERIC tax_amount
        NUMERIC discount_amount
        NUMERIC total_amount
        VARCHAR raw_vendor_name
        VARCHAR raw_date_text
        VARCHAR raw_total_text
        NUMERIC confidence
        TEXT notes
        TIMESTAMPTZ extracted_at
        TIMESTAMPTZ updated_at
    }

    invoice_line_items {
        UUID id PK
        UUID invoice_id FK
        SMALLINT line_number
        TEXT description
        NUMERIC quantity
        NUMERIC unit_price
        NUMERIC line_total
        NUMERIC tax_rate
        NUMERIC tax_amount
        VARCHAR sku
        VARCHAR unit_of_measure
        TIMESTAMPTZ created_at
    }

    bank_statements {
        UUID id PK
        UUID document_id FK
        UUID processing_job_id FK
        VARCHAR bank_name
        VARCHAR account_number
        VARCHAR account_holder
        CHAR currency
        DATE statement_from
        DATE statement_to
        NUMERIC opening_balance
        NUMERIC closing_balance
        JSON raw_headers
        VARCHAR detected_format
        TIMESTAMPTZ extracted_at
        TIMESTAMPTZ updated_at
    }

    bank_transactions {
        UUID id PK
        UUID bank_statement_id FK
        DATE transaction_date
        DATE value_date
        TEXT description
        TEXT raw_description
        VARCHAR reference_number
        ENUM transaction_type
        NUMERIC amount
        CHAR direction
        NUMERIC balance_after
        CHAR currency
        INTEGER row_index
        TIMESTAMPTZ created_at
    }

    %% Relationships
    documents       ||--o{ processing_jobs    : "triggers"
    documents       ||--o| invoices           : "parsed into"
    documents       ||--o| bank_statements    : "parsed into"


    processing_jobs ||--o| invoices           : "produced by"
    processing_jobs ||--o| bank_statements    : "produced by"

    vendors         ||--o{ invoices           : "billed via"

    invoices        ||--o{ invoice_line_items : "contains"

    bank_statements ||--o{ bank_transactions  : "contains"
```

---

## 3. Database Constraints & Money Safety Rules

1. **Money Representation**: Float datatypes are forbidden. Numeric/Decimal (`sa.Numeric(18, 4)`) is used for all subtotals, tax rates, balances, and transaction amounts.
2. **Sign Enforcement**: Amounts are stored unsigned (`amount >= 0`) combined with an explicit check constraint: `CHECK (direction IN ('C', 'D'))` to eliminate signs mismatch bugs.
3. **Soft-deletion Consistency**: `CHECK ((is_deleted = FALSE AND deleted_at IS NULL) OR (is_deleted = TRUE AND deleted_at IS NOT NULL))` protects documents state.
4. **Ordering Integrity**: A unique index on `(invoice_id, line_number)` for invoice line items and `(bank_statement_id, row_index)` for statement transactions prevents accidental duplicate insert overlays.
