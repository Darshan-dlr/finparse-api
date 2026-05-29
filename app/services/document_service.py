"""
DocumentService — orchestrates upload, validation, parsing, and persistence.
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import FinParseException, OCRFailedError
from app.core.logging import get_logger
from app.models.bank_statement import BankStatement, BankTransaction
from app.models.document import Document, DocumentType
from app.models.processing_job import JobStatus, ProcessingJob
from app.models.invoice import Invoice, InvoiceLineItem, Vendor
from app.parsers.csv_parser import CSVParser, ParsedBankStatement, ParsedTransaction
from app.parsers.pdf_parser import PDFParser, ParsedInvoice, ParsedInvoiceLineItem
from app.validators.file_validator import FileValidator, ValidatedFile

logger = get_logger(__name__)
settings = get_settings()


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ══════════════════════════════════════════════════════════════════════════
    # Upload
    # ══════════════════════════════════════════════════════════════════════════

    async def upload_and_enqueue(
        self,
        file: UploadFile,
        pdf_password: str | None,
        allow_reprocess: bool,
        uploaded_by: str = "system",
    ) -> dict:
        """
        Full upload pipeline:
        1. Validate file (3-stage validator)
        2. Save to storage
        3. Create Document + ProcessingJob rows
        4. Parse CSV immediately (or enqueue for PDF)
        5. Return job status
        """
        # ── Validate ──────────────────────────────────────────────────────────
        validator = FileValidator(checksum_lookup_fn=self._lookup_by_checksum)
        validated: ValidatedFile = await validator.validate(
            file=file,
            pdf_password=pdf_password,
            allow_reprocess=allow_reprocess,
        )

        # ── Check if duplicate + allow_reprocess ──────────────────────────────
        existing_doc = await self._find_by_checksum(validated.checksum_sha256)
        is_reprocess = existing_doc is not None and allow_reprocess

        # ── Save to storage ───────────────────────────────────────────────────
        if not is_reprocess:
            storage_path = await self._save_to_storage(validated)
            document = await self._create_document(validated, storage_path, uploaded_by)
        else:
            document = existing_doc

        # ── Create ProcessingJob ──────────────────────────────────────────────
        job = await self._create_job(document, validated, is_reprocess)

        # ── Parse immediately (sync for now; move to Celery for prod) ─────────
        if validated.file_extension == ".csv":
            await self._parse_csv(job, document, validated)
        elif validated.file_extension == ".pdf":
            await self._parse_pdf(job, document, validated)

        await self.db.flush()

        return {
            "document_id": str(document.id),
            "job_id": str(job.id),
            "status": job.status.value,
            "is_reprocess": is_reprocess,
            "message": (
                "File accepted and parsed."
                if job.status == JobStatus.COMPLETED
                else f"File accepted. Status: {job.status.value}"
            ),
        }

    # ══════════════════════════════════════════════════════════════════════════
    # CSV Parsing
    # ══════════════════════════════════════════════════════════════════════════

    async def _parse_csv(
        self,
        job: ProcessingJob,
        document: Document,
        validated: ValidatedFile,
    ) -> None:
        """Run CSV parser and persist results. Updates job status."""
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.now(timezone.utc)

        try:
            parser = CSVParser(max_rows=settings.max_csv_rows)
            result: ParsedBankStatement = parser.parse(validated.content)

            # ── Persist BankStatement ─────────────────────────────────────────
            statement = BankStatement(
                document_id=document.id,
                processing_job_id=job.id,
                bank_name=result.bank_name,
                account_number=result.account_number,
                account_holder=result.account_holder,
                currency=result.currency,
                statement_from=result.statement_from,
                statement_to=result.statement_to,
                opening_balance=result.opening_balance,
                closing_balance=result.closing_balance,
                raw_headers=result.raw_headers,
                detected_format=result.detected_format,
                detected_delimiter=result.detected_delimiter,
                detected_encoding=result.detected_encoding,
                total_rows_parsed=result.total_rows_parsed,
                total_rows_skipped=result.total_rows_skipped,
            )
            self.db.add(statement)
            await self.db.flush()  # Get statement.id

            # ── Persist Transactions ──────────────────────────────────────────
            tx_objects = [
                self._build_transaction(statement.id, tx)
                for tx in result.transactions
            ]
            self.db.add_all(tx_objects)

            # ── Update job ────────────────────────────────────────────────────
            job.status = JobStatus.COMPLETED if not result.warnings else JobStatus.PARTIAL
            job.completed_at = datetime.now(timezone.utc)
            job.parser_version = result.parser_version
            job.warnings = result.warnings or None

            logger.info(
                "CSV parse completed",
                job_id=str(job.id),
                transactions=result.total_rows_parsed,
                warnings=len(result.warnings),
                status=job.status.value,
            )

        except FinParseException as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = e.message
            job.error_detail = {"error_code": e.error_code, **e.detail}
            logger.warning("CSV parse failed (known error)", error_code=e.error_code, message=e.message)

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = str(e)
            job.error_detail = {"stage": "csv_parsing", "error": str(e)}
            logger.exception("CSV parse failed (unexpected)", error=str(e))

    # ══════════════════════════════════════════════════════════════════════════
    # PDF Parsing
    # ══════════════════════════════════════════════════════════════════════════

    async def _parse_pdf(
        self,
        job: ProcessingJob,
        document: Document,
        validated: ValidatedFile,
    ) -> None:
        """Run PDF parser and persist results. Updates job status."""
        job.status = JobStatus.PROCESSING
        job.started_at = datetime.now(timezone.utc)

        try:
            # ── Check if OCR needed ───────────────────────────────────────────
            if validated.ocr_needed:
                raise OCRFailedError(
                    "OCR is required for this scanned PDF but OCR is not configured/implemented."
                )

            parser = PDFParser()
            result: ParsedInvoice = parser.parse(validated.content)

            # ── Deduplicate / Find or Create Vendor ───────────────────────────
            vendor_id = None
            if result.raw_vendor_name:
                vendor_canonical = result.raw_vendor_name.strip()
                # Find vendor case-insensitively
                stmt_vendor = select(Vendor).where(
                    func.lower(Vendor.canonical_name) == func.lower(vendor_canonical)
                )
                vendor_result = await self.db.execute(stmt_vendor)
                vendor = vendor_result.scalar_one_or_none()

                if not vendor:
                    vendor = Vendor(
                        canonical_name=vendor_canonical,
                        raw_names=[vendor_canonical],
                    )
                    self.db.add(vendor)
                    await self.db.flush()  # get vendor.id
                
                vendor_id = vendor.id

            # ── Persist Invoice ───────────────────────────────────────────────
            invoice = Invoice(
                document_id=document.id,
                processing_job_id=job.id,
                vendor_id=vendor_id,
                invoice_number=result.invoice_number,
                invoice_date=result.invoice_date,
                due_date=result.due_date,
                currency=result.currency,
                subtotal=result.subtotal,
                tax_amount=result.tax_amount,
                discount_amount=result.discount_amount,
                total_amount=result.total_amount,
                raw_vendor_name=result.raw_vendor_name,
                raw_date_text=result.raw_date_text,
                raw_total_text=result.raw_total_text,
                confidence=result.confidence,
                notes=result.notes,
                page_range_start=1,
                page_range_end=validated.pdf_page_count or 1,
                invoice_index=0,
            )
            self.db.add(invoice)
            await self.db.flush()  # get invoice.id

            # ── Persist Line Items ────────────────────────────────────────────
            line_objects = []
            for item in result.line_items:
                line_obj = InvoiceLineItem(
                    invoice_id=invoice.id,
                    line_number=item.line_number,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    line_total=item.line_total,
                    tax_rate=item.tax_rate,
                    tax_amount=item.tax_amount,
                    sku=item.sku,
                    unit_of_measure=item.unit_of_measure,
                )
                line_objects.append(line_obj)

            if line_objects:
                self.db.add_all(line_objects)

            # ── Update job ────────────────────────────────────────────────────
            job.status = JobStatus.COMPLETED if not result.warnings else JobStatus.PARTIAL
            job.completed_at = datetime.now(timezone.utc)
            job.parser_version = result.parser_version
            job.warnings = [{"warning": w} for w in result.warnings] if result.warnings else None

            logger.info(
                "PDF invoice parse completed",
                job_id=str(job.id),
                line_items=len(result.line_items),
                warnings=len(result.warnings),
                status=job.status.value,
            )

        except FinParseException as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = e.message
            job.error_detail = {"error_code": e.error_code, **e.detail}
            logger.warning("PDF parse failed (known error)", error_code=e.error_code, message=e.message)

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = str(e)
            job.error_detail = {"stage": "pdf_parsing", "error": str(e)}
            logger.exception("PDF parse failed (unexpected)", error=str(e))

    def _build_transaction(
        self, statement_id: uuid.UUID, tx: ParsedTransaction
    ) -> BankTransaction:
        return BankTransaction(
            bank_statement_id=statement_id,
            transaction_date=tx.transaction_date,
            value_date=tx.value_date,
            description=tx.description,
            raw_description=tx.raw_description,
            reference_number=tx.reference_number,
            transaction_type=tx.transaction_type,
            amount=tx.amount,
            direction=tx.direction,
            balance_after=tx.balance_after,
            currency=tx.currency,
            row_index=tx.row_index,
            parse_warnings=tx.parse_warnings if tx.parse_warnings else None,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Retrieval
    # ══════════════════════════════════════════════════════════════════════════

    async def get_document(self, document_id: uuid.UUID) -> Document:
        doc = await self.db.get(Document, document_id)
        if not doc or doc.is_deleted:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Document not found")
        return doc

    async def get_latest_job(self, document_id: uuid.UUID) -> ProcessingJob:
        result = await self.db.execute(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if not job:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="No processing job found")
        return job

    async def list_documents(self, **filters) -> dict:
        query = select(Document).where(Document.is_deleted == False)

        if filters.get("document_type"):
            query = query.where(Document.document_type == filters["document_type"])

        query = query.limit(filters.get("limit", 20)).offset(filters.get("offset", 0))
        result = await self.db.execute(query)
        docs = result.scalars().all()
        return {"items": docs, "count": len(docs)}

    async def soft_delete(self, document_id: uuid.UUID) -> None:
        doc = await self.db.get(Document, document_id)
        if not doc or doc.is_deleted:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Document not found")
        doc.is_deleted = True
        doc.deleted_at = datetime.now(timezone.utc)

    # ══════════════════════════════════════════════════════════════════════════
    # Storage
    # ══════════════════════════════════════════════════════════════════════════

    async def _save_to_storage(self, validated: ValidatedFile) -> str:
        """Save file to local disk (swap for S3 client when ready)."""
        storage_dir = Path(settings.storage_path)
        storage_dir.mkdir(parents=True, exist_ok=True)

        unique_name = f"{uuid.uuid4().hex}_{validated.safe_filename}"
        path = storage_dir / unique_name
        path.write_bytes(validated.content)
        return str(path)

    # ══════════════════════════════════════════════════════════════════════════
    # DB helpers
    # ══════════════════════════════════════════════════════════════════════════

    async def _lookup_by_checksum(self, checksum: str) -> uuid.UUID | None:
        result = await self.db.execute(
            select(Document.id).where(
                Document.checksum_sha256 == checksum,
                Document.is_deleted == False,
            )
        )
        return result.scalar_one_or_none()

    async def _find_by_checksum(self, checksum: str) -> Document | None:
        result = await self.db.execute(
            select(Document).where(
                Document.checksum_sha256 == checksum,
                Document.is_deleted == False,
            )
        )
        return result.scalar_one_or_none()

    async def _create_document(self, validated: ValidatedFile, storage_path: str, uploaded_by: str = "system") -> Document:
        doc_type = (
            DocumentType.INVOICE
            if validated.file_extension == ".pdf"
            else DocumentType.BANK_STATEMENT
        )
        doc = Document(
            filename=validated.safe_filename,
            original_name=validated.original_filename,
            document_type=doc_type,
            file_type=validated.file_extension.lstrip("."),
            file_size_bytes=validated.file_size_bytes,
            checksum_sha256=validated.checksum_sha256,
            storage_path=storage_path,
            uploaded_by=uploaded_by,
        )
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def _create_job(
        self,
        document: Document,
        validated: ValidatedFile,
        is_reprocess: bool,
    ) -> ProcessingJob:
        job = ProcessingJob(
            document_id=document.id,
            status=JobStatus.PENDING,
            is_reprocess=is_reprocess,
            pdf_password_used=getattr(validated, "pdf_password_used", False),
            pdf_encryption_type=getattr(validated, "pdf_encryption_type", None),
            ocr_used=False,
            scanned_pages=getattr(validated, "scanned_pages", None),
        )
        self.db.add(job)
        await self.db.flush()
        return job

    # ══════════════════════════════════════════════════════════════════════════
    # Serializers
    # ══════════════════════════════════════════════════════════════════════════

    def _serialize_document(self, doc: Document) -> dict:
        return {
            "id": str(doc.id),
            "original_name": doc.original_name,
            "document_type": doc.document_type.value,
            "file_type": doc.file_type,
            "file_size_bytes": doc.file_size_bytes,
            "uploaded_at": doc.uploaded_at.isoformat(),
        }

    def _serialize_job(self, job: ProcessingJob) -> dict:
        return {
            "id": str(job.id),
            "document_id": str(job.document_id),
            "status": job.status.value,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "error_message": job.error_message,
            "warnings": job.warnings,
            "parser_version": job.parser_version,
            "is_reprocess": job.is_reprocess,
        }
