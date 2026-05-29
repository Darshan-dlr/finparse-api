"""
Import all models so SQLAlchemy can discover them for metadata.create_all().
"""
from app.models.base import Base
from app.models.document import Document, DocumentType
from app.models.processing_job import ProcessingJob, JobStatus
from app.models.bank_statement import BankStatement, BankTransaction, TransactionType
from app.models.document_tag import DocumentTag
from app.models.invoice import Vendor, Invoice, InvoiceLineItem

__all__ = [
    "Base",
    "Document", "DocumentType",
    "ProcessingJob", "JobStatus",
    "BankStatement", "BankTransaction", "TransactionType",
    "DocumentTag",
    "Vendor", "Invoice", "InvoiceLineItem",
]
