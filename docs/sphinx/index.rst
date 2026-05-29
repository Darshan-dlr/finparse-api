.. FinParse API documentation master file

FinParse API Documentation 🏦
=============================

Welcome to the FinParse API developer portal! 🚀
-----------------------------------------------

FinParse is a production-grade, highly-extensible parsing engine built with **FastAPI** to process financial documents (PDF invoices and CSV/Excel bank statements) into clean, structured relational data.

Features at a Glance
--------------------

* 🔍 **Smart Format Recognition**: Automatically identifies document types and handles variations across different banks and vendors.
* 🛡️ **3-Stage File Validation**: Strict validation checks (Extension → MIME Type → File Content Headers) preventing malicious or invalid file uploads.
* ⚡ **Polymorphic Parser Factory**: Implemented with the Strategy and Factory design patterns, making it trivial to extend parsing logic to new formats.
* 📊 **Reconciliation & Mathematical Audit**: Auto-calculates invoice subtotals, taxes, and transaction directions, generating descriptive system warnings on mismatches.
* 💾 **Case-Insensitive Vendor Deduplication**: Automatically resolves vendor variations (e.g. "Amazon Inc" vs "Amazon") to a canonical vendor registry.
* ⚙️ **Fully Automated CI/CD & Docs**: Automatic testing suite runs alongside Sphinx compilation, auto-deploying documentation pages.

.. toctree::
   :maxdepth: 2
   :caption: 📖 Developer Guides:

   System Design & ER Diagram <../../docs/db_design_and_er>
   Database Schema Design <../../docs/schema_design>
   CSV Parser Manual Testing Log <../../docs/manual_testing_log>
   PDF Parser Manual Testing Log <../../docs/pdf_manual_testing_log>

.. toctree::
   :maxdepth: 3
   :caption: 🛠️ API Reference:

   modules

Indices and Tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
