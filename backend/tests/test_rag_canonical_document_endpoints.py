"""Tests for canonical document endpoints."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from app.core.rag_ingestion_store import (
    save_acquisition_report,
    save_job,
    save_source_record,
)
from app.models.rag_ingestion import (
    AcquisitionStatus,
    DuplicateStatus,
    JobStatus,
    ParseStatus,
    RagAcquisitionReport,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
)


def _source_record(**kwargs) -> RagSourceRecord:
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


def _job(record: RagSourceRecord) -> RagIngestionJob:
    return RagIngestionJob(
        rag_pack_id=record.rag_pack_id,
        collection_id=record.collection_id,
        domain=record.domain,
        source_id=record.source_id,
        status=JobStatus.QUEUED,
        dry_run=True,
        duplicate_status=DuplicateStatus.UNIQUE,
    )


def _report(record: RagSourceRecord, text: str, job_id: str) -> RagAcquisitionReport:
    return RagAcquisitionReport(
        job_id=job_id,
        source_id=record.source_id,
        rag_pack_id=record.rag_pack_id,
        collection_id=record.collection_id,
        domain=record.domain,
        source_uri=record.source_uri,
        source_class=record.source_class,
        status=AcquisitionStatus.PARSED,
        parse_status=ParseStatus.PARSED,
        computed_content_hash=hashlib.sha256(b"raw").hexdigest(),
        parser_id="plaintext",
        extracted_text=text,
        duplicate_status=DuplicateStatus.UNIQUE,
    )


def test_create_canonical_document_endpoint(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "Hello, world! This is a test.", job.job_id)
    save_acquisition_report(report)

    response = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["canonicalization_status"] == "canonicalized"
    assert data["chunking_status"] == "chunked"
    assert data["index_status"] == "not_indexed"


def test_create_canonical_document_non_dry_run_rejected(
    client: TestClient, tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "text", job.job_id)
    save_acquisition_report(report)

    response = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": False, "create_chunks": True},
    )
    assert response.status_code == 409


def test_create_canonical_document_idempotent(
    client: TestClient, tmp_path, monkeypatch
):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "text for idempotency", job.job_id)
    save_acquisition_report(report)

    url = f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document"
    r1 = client.post(url, json={"dry_run": True, "create_chunks": True})
    r2 = client.post(url, json={"dry_run": True, "create_chunks": True})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["document_id"] == r2.json()["document_id"]


def test_get_canonical_document(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "Get me.", job.job_id)
    save_acquisition_report(report)

    create_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )
    document_id = create_resp.json()["document_id"]

    response = client.get(f"/v1/domains/legal/rag-ingestion/documents/{document_id}")
    assert response.status_code == 200
    assert response.json()["document_id"] == document_id


def test_get_canonical_document_text(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "Canonical text content.", job.job_id)
    save_acquisition_report(report)

    create_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )
    document_id = create_resp.json()["document_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/{document_id}/text"
    )
    assert response.status_code == 200
    assert "Canonical text content" in response.text


def test_list_canonical_documents(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "List me.", job.job_id)
    save_acquisition_report(report)

    client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )

    response = client.get("/v1/domains/legal/rag-ingestion/documents")
    assert response.status_code == 200
    assert len(response.json()["documents"]) == 1


def test_list_chunks(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    text = " ".join(f"sentence{i}" for i in range(50))
    report = _report(record, text, job.job_id)
    save_acquisition_report(report)

    create_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )
    document_id = create_resp.json()["document_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/{document_id}/chunks"
    )
    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert len(chunks) > 0
    ordinals = [c["ordinal"] for c in chunks]
    assert ordinals == sorted(ordinals)


def test_get_chunk(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    text = " ".join(f"sentence{i}" for i in range(50))
    report = _report(record, text, job.job_id)
    save_acquisition_report(report)

    create_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )
    document_id = create_resp.json()["document_id"]

    chunks_resp = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/{document_id}/chunks"
    )
    chunk_id = chunks_resp.json()["chunks"][0]["chunk_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/{document_id}/chunks/{chunk_id}"
    )
    assert response.status_code == 200
    assert response.json()["chunk_id"] == chunk_id


def test_wrong_domain_returns_404(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    save_source_record(record)
    job = _job(record)
    save_job(job)
    report = _report(record, "text", job.job_id)
    save_acquisition_report(report)

    create_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/jobs/{job.job_id}/acquisition-previews/{report.acquisition_id}/canonical-document",
        json={"dry_run": True, "create_chunks": True},
    )
    document_id = create_resp.json()["document_id"]

    response = client.get(f"/v1/domains/finance/rag-ingestion/documents/{document_id}")
    assert response.status_code == 404
