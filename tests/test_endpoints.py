"""
Integration tests for FastAPI endpoints.
"""
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient):
    """Test health check route."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_upload_and_retrieve_bank_statement(
    async_client: AsyncClient, standard_csv_bytes: bytes
):
    """Test full upload, parsing, job retrieval, and document get flow."""
    # ── Upload ────────────────────────────────────────────────────────────────
    files = {"file": ("standard_statement.csv", standard_csv_bytes, "text/csv")}
    response = await async_client.post("/api/v1/documents/upload", files=files)
    assert response.status_code == 202
    
    data = response.json()
    assert "document_id" in data
    assert "job_id" in data
    assert data["status"] in ("completed", "partial")  # CSV parser runs synchronously in our current service implementation

    doc_id = data["document_id"]
    job_id = data["job_id"]

    # ── Get Document ──────────────────────────────────────────────────────────
    doc_response = await async_client.get(f"/api/v1/documents/{doc_id}")
    assert doc_response.status_code == 200
    doc_data = doc_response.json()
    assert doc_data["id"] == doc_id
    assert doc_data["original_name"] == "standard_statement.csv"
    assert doc_data["document_type"] == "bank_statement"
    assert doc_data["file_type"] == "csv"

    # ── Get Job Status ────────────────────────────────────────────────────────
    job_response = await async_client.get(f"/api/v1/documents/{doc_id}/job")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["id"] == job_id
    assert job_data["document_id"] == doc_id
    assert job_data["status"] in ("completed", "partial")

    # ── List Documents ────────────────────────────────────────────────────────
    list_response = await async_client.get("/api/v1/documents/")
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert list_data["count"] == 1
    assert list_data["items"][0]["id"] == doc_id

    # ── Duplicate Rejection (allow_reprocess = False) ─────────────────────────
    # Re-uploading exact same file bytes should raise 409
    dup_response = await async_client.post("/api/v1/documents/upload", files=files)
    assert dup_response.status_code == 409
    assert dup_response.json()["error"] == "DUPLICATE_FILE"

    # ── Duplicate Reprocessing (allow_reprocess = True) ────────────────────────
    reproc_response = await async_client.post(
        f"/api/v1/documents/upload?allow_reprocess=true", files=files
    )
    assert reproc_response.status_code == 202
    reproc_data = reproc_response.json()
    assert reproc_data["document_id"] == doc_id
    assert reproc_data["job_id"] != job_id  # New job ID created
    assert reproc_data["is_reprocess"] is True

    # ── Delete Document (Soft Delete) ─────────────────────────────────────────
    delete_response = await async_client.delete(f"/api/v1/documents/{doc_id}")
    assert delete_response.status_code == 204

    # Get after delete should return 404
    get_after_delete = await async_client.get(f"/api/v1/documents/{doc_id}")
    assert get_after_delete.status_code == 404
