"""Tests for the RAG acquisition preview endpoints."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.core.rag_ingestion_store import save_job, save_source_record
from app.models.rag_ingestion import (
    DuplicateStatus,
    JobStatus,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
)

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


def _legal_record(**kwargs) -> RagSourceRecord:
    defaults = {
        "rag_pack_id": "legal_core_rag",
        "collection_id": "prebuilt_legal_core",
        "domain": "legal",
        "source_class": SourceClass.PUBLIC_DOMAIN,
        "title": "Sample",
        "source_uri": "https://example.com/sample.txt",
        "license_review_status": "approved",
        "authority_rating": "high",
        "content_hash": None,
        "external_document_id": "doc-1",
    }
    defaults.update(kwargs)
    return RagSourceRecord(**defaults)


def _legal_job(record: RagSourceRecord) -> RagIngestionJob:
    return RagIngestionJob(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_id=record.source_id,
        status=JobStatus.QUEUED,
        dry_run=True,
        duplicate_status=DuplicateStatus.UNIQUE,
    )


def _transport_for_text(body: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/plain"},
        )

    return httpx.MockTransport(handler)


def test_acquisition_preview_success(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    body = b"endpoint test document"
    transport = _transport_for_text(body)
    with patch(PATCH_TARGET, return_value=LEGAL_PACK), patch(
        "app.routers.domains.run_acquisition_preview"
    ) as mock_run:
        from app.core.rag_source_acquisition import run_acquisition_preview as real_run

        mock_run.side_effect = lambda *args, **kwargs: real_run(
            *args, **{**kwargs, "client": httpx.Client(transport=transport)}
        )
        response = client.post(
            f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-preview",
            json={"dry_run": True, "parse": True},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "parsed"
    assert data["computed_content_hash"] == hashlib.sha256(body).hexdigest()


def test_acquisition_preview_non_dry_run_rejected(
    client: TestClient, tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-preview",
            json={"dry_run": False, "parse": True},
        )
    assert response.status_code == 409
    assert "not enabled" in response.json()["detail"]["message"].lower()


def test_acquisition_preview_unknown_job(client: TestClient):
    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            "/v1/domains/legal/rag-ingestion/jobs/nonexistent/acquisition-preview",
            json={"dry_run": True, "parse": True},
        )
    assert response.status_code == 422


def test_acquisition_preview_wrong_domain(
    client: TestClient, tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    with patch(PATCH_TARGET, return_value=LEGAL_PACK):
        response = client.post(
            f"/v1/domains/finance/rag-ingestion/jobs/{job.job_id}/acquisition-preview",
            json={"dry_run": True, "parse": True},
        )
    assert response.status_code == 422


def test_list_acquisition_previews(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    body = b"list preview test"
    transport = _transport_for_text(body)
    with patch(PATCH_TARGET, return_value=LEGAL_PACK), patch(
        "app.routers.domains.run_acquisition_preview"
    ) as mock_run:
        from app.core.rag_source_acquisition import run_acquisition_preview as real_run

        mock_run.side_effect = lambda *args, **kwargs: real_run(
            *args, **{**kwargs, "client": httpx.Client(transport=transport)}
        )
        client.post(
            f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-preview",
            json={"dry_run": True, "parse": True},
        )

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "legal"
    assert data["job_id"] == job.job_id
    assert len(data["acquisitions"]) == 1


def test_get_acquisition_preview(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    body = b"get preview test"
    transport = _transport_for_text(body)
    with patch(PATCH_TARGET, return_value=LEGAL_PACK), patch(
        "app.routers.domains.run_acquisition_preview"
    ) as mock_run:
        from app.core.rag_source_acquisition import run_acquisition_preview as real_run

        mock_run.side_effect = lambda *args, **kwargs: real_run(
            *args, **{**kwargs, "client": httpx.Client(transport=transport)}
        )
        create_resp = client.post(
            f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-preview",
            json={"dry_run": True, "parse": True},
        )
    acquisition_id = create_resp.json()["acquisition_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{acquisition_id}"
    )
    assert response.status_code == 200
    assert response.json()["acquisition_id"] == acquisition_id


def test_get_acquisition_preview_wrong_domain(
    client: TestClient, tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    body = b"wrong domain test"
    transport = _transport_for_text(body)
    with patch(PATCH_TARGET, return_value=LEGAL_PACK), patch(
        "app.routers.domains.run_acquisition_preview"
    ) as mock_run:
        from app.core.rag_source_acquisition import run_acquisition_preview as real_run

        mock_run.side_effect = lambda *args, **kwargs: real_run(
            *args, **{**kwargs, "client": httpx.Client(transport=transport)}
        )
        create_resp = client.post(
            f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-preview",
            json={"dry_run": True, "parse": True},
        )
    acquisition_id = create_resp.json()["acquisition_id"]

    response = client.get(
        f"/v1/domains/finance/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{acquisition_id}"
    )
    assert response.status_code == 404
