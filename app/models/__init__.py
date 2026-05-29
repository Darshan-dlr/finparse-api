"""
Import all models so SQLAlchemy can discover them for metadata.create_all().
"""
from app.models.base import Base
from app.models.enums import DocumentType, JobStatus, TransactionType
from app.models.document import Document
from app.models.processing_job import ProcessingJob
from app.models.bank_statement import BankStatement, BankTransaction
from app.models.invoice import Vendor, Invoice, InvoiceLineItem

__all__ = [
    "Base",
    "Document", "DocumentType",
    "ProcessingJob", "JobStatus",
    "BankStatement", "BankTransaction", "TransactionType",
    "Vendor", "Invoice", "InvoiceLineItem",
]
