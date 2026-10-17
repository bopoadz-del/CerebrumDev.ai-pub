"""Tests for the /v1/domains/{domain_id}/rag-ingestion endpoints."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

PATCH_TARGET = "app.core.rag_ingestion_validation.get_rag_pack"


LEGAL_PACK = {
    "id": "legal_core_rag",
    "domain": "legal",
    "collection_id": "prebuilt_legal_core",
    "source_policy": {
        "allowed_source_classes": [
            "public_domain",
            "open_license",
            "official_statute_or_regulation",
            "official_guidance",
            "platform_curated_template",
        ],
        "precluded_source_classes": [
            "private_enterprise_data",
            "confidential_client_data",
            "copyrighted_commercial_content_without_license",
            "user_uploaded_project_records",
            "unknown_license",
        ],
        "requires_source_record": True,
        "requires_license_review": True,
        "requires_authority_rating": True,
    },
}


def _valid_payload(dry_run: bool = True, queue: bool = False) -> dict:
    return {
        "rag_pack_id": "legal_core_rag",
        "collection_id": "prebuilt_legal_core",
        "source_class": "public_domain",
        "title": "US Copyright Act",
        "source_uri": "https://example.com/copyright-act",
        "publisher": "US Government",
        "license_name": "Public Domain",
        "license_review_status": "approved",
        "authority_rating": "high",
        "content_hash": "hash-endpoint-1",
        "external_document_id": "doc-endpoint-1",
        "dry_run": dry_run,
        "queue": queue,
    }


def test_create_dry_run_validation_only(client: TestClient, tmp_path, monkeypatch):
    """dry_run=true, queue=false returns a validated job without queueing."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs",
            json=_valid_payload(dry_run=True, queue=False),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validated"
    assert data["dry_run"] is True
    assert data["duplicate_status"] == "unique"


def test_create_dry_run_queue(client: TestClient, tmp_path, monkeypatch):
    """dry_run=true, queue=true returns a queued job that is not executed."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs",
            json=_valid_payload(dry_run=True, queue=True),
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["queued_at"] is not None


def test_non_dry_run_rejected(client: TestClient, tmp_path, monkeypatch):
    """dry_run=false returns 409 because actual ingestion is disabled."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    response = client.post(
        "/v1/domains/legal/rag-ingestion/jobs",
        json=_valid_payload(dry_run=False),
    )
    assert response.status_code == 409
    assert "not enabled" in response.json()["detail"].lower()


def test_unknown_rag_pack_fails(client: TestClient, tmp_path, monkeypatch):
    """An unknown domain/pack produces a validation_failed job."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=None):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(e["code"] == "RAG_PACK_NOT_FOUND" for e in data["validation_errors"])


def _get_pack(domain: str):
    if domain == "legal":
        return LEGAL_PACK
    return None


def test_domain_mismatch_fails(client: TestClient, tmp_path, monkeypatch):
    """A request routed for the wrong domain fails validation."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, side_effect=_get_pack):
        response = client.post(
            "/v1/domains/finance/rag-ingestion/jobs", json=_valid_payload()
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"


def test_collection_id_mismatch_fails(client: TestClient, tmp_path, monkeypatch):
    """A collection_id that does not match the pack fails validation."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    payload = _valid_payload()
    payload["collection_id"] = "wrong"
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs", json=payload
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(
        e["code"] == "COLLECTION_ID_MISMATCH" for e in data["validation_errors"]
    )


def test_precluded_source_class_fails(client: TestClient, tmp_path, monkeypatch):
    """A precluded source class produces a validation_failed job."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    payload = _valid_payload()
    payload["source_class"] = "unknown_license"
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs", json=payload
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(
        e["code"] in {"SOURCE_CLASS_PRECLUDED", "SOURCE_CLASS_NOT_ALLOWED"}
        for e in data["validation_errors"]
    )


def test_missing_license_review_fails(client: TestClient, tmp_path, monkeypatch):
    """Missing license review produces a validation_failed job."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    payload = _valid_payload()
    payload["license_review_status"] = None
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs", json=payload
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(
        e["code"] == "LICENSE_REVIEW_REQUIRED" for e in data["validation_errors"]
    )


def test_duplicate_content_hash_detected(client: TestClient, tmp_path, monkeypatch):
    """A duplicate content_hash in the same collection fails validation."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload())
        payload = _valid_payload()
        payload["external_document_id"] = "doc-endpoint-2"
        payload["source_uri"] = "https://example.com/other"
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs", json=payload
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(
        e["code"] == "DUPLICATE_CONTENT_HASH" for e in data["validation_errors"]
    )


def test_get_job(client: TestClient, tmp_path, monkeypatch):
    """A created job can be retrieved by domain and job id."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        create_resp = client.post(
            "/v1/domains/legal/rag-ingestion/jobs",
            json=_valid_payload(queue=True),
        )
    job_id = create_resp.json()["job_id"]
    get_resp = client.get(f"/v1/domains/legal/rag-ingestion/jobs/{job_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["job_id"] == job_id


def test_get_job_wrong_domain_returns_404(client: TestClient, tmp_path, monkeypatch):
    """Jobs are scoped to their domain route."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        create_resp = client.post(
            "/v1/domains/legal/rag-ingestion/jobs",
            json=_valid_payload(queue=True),
        )
    job_id = create_resp.json()["job_id"]
    get_resp = client.get(f"/v1/domains/finance/rag-ingestion/jobs/{job_id}")
    assert get_resp.status_code == 404


def test_unknown_job_returns_404(client: TestClient):
    """A missing job id returns 404."""
    response = client.get("/v1/domains/legal/rag-ingestion/jobs/nonexistent")
    assert response.status_code == 404


def test_list_jobs(client: TestClient, tmp_path, monkeypatch):
    """The list endpoint returns jobs for the domain."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        client.post(
            "/v1/domains/legal/rag-ingestion/jobs",
            json=_valid_payload(queue=True),
        )
    response = client.get("/v1/domains/legal/rag-ingestion/jobs")
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "legal"
    assert len(data["jobs"]) == 1


def test_list_jobs_filter_by_status(client: TestClient, tmp_path, monkeypatch):
    """The list endpoint filters by status query param."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        client.post(
            "/v1/domains/legal/rag-ingestion/jobs",
            json=_valid_payload(queue=True),
        )
    response = client.get("/v1/domains/legal/rag-ingestion/jobs?status=queued")
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 1
    response = client.get("/v1/domains/legal/rag-ingestion/jobs?status=validated")
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 0
