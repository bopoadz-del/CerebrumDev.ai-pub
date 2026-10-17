"""Tests for the RAG source acquisition adapter."""

from __future__ import annotations

import hashlib
import secrets
from unittest.mock import patch

import httpx
import pytest

from app.core.rag_ingestion_store import (
    get_acquisition_report,
    save_job,
    save_source_record,
)
from app.core.rag_source_acquisition import (
    AcquisitionError,
    fetch_source,
    run_acquisition_preview,
    validate_source_uri,
)
from app.models.rag_ingestion import (
    AcquisitionStatus,
    DuplicateStatus,
    JobStatus,
    ParseStatus,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
)


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
        "source_uri": "https://example.com/sample.pdf",
        "license_review_status": "approved",
        "authority_rating": "high",
        "content_hash": None,
        "external_document_id": "doc-1",
    }
    defaults.update(kwargs)
    return RagSourceRecord(**defaults)


def _legal_job(record: RagSourceRecord, status: JobStatus = JobStatus.QUEUED) -> RagIngestionJob:
    return RagIngestionJob(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_id=record.source_id,
        status=status,
        dry_run=True,
        duplicate_status=DuplicateStatus.UNIQUE,
    )


def _pdf_response(text: bytes = b"%PDF-1.4 test") -> httpx.Response:
    return httpx.Response(
        200,
        content=text,
        headers={"content-type": "application/pdf"},
    )


def _transport_for_response(response: httpx.Response) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    return httpx.MockTransport(handler)


# --- URL validation tests ---

def test_validate_source_uri_accepts_https():
    validate_source_uri("https://example.com/file.pdf")


_URI_PW = secrets.token_hex(8)  # generated per run


@pytest.mark.parametrize(
    "uri,expected_code",
    [
        ("http://example.com/file.pdf", "SOURCE_URI_INVALID"),
        ("ftp://example.com/file.pdf", "SOURCE_URI_INVALID"),
        ("file:///etc/passwd", "SOURCE_URI_INVALID"),
        (f"https://user:{_URI_PW}@example.com/file.pdf", "SOURCE_URI_INVALID"),
        ("https://127.0.0.1/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://localhost/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://0.0.0.0/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://10.0.0.1/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://192.168.1.1/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://172.16.0.1/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://169.254.169.254/latest/meta-data", "SOURCE_URI_BLOCKED"),
        ("https://[::1]/file.pdf", "SOURCE_URI_BLOCKED"),
        ("https://[fe80::1]/file.pdf", "SOURCE_URI_BLOCKED"),
    ],
)
def test_validate_source_uri_blocks_bad_uris(uri: str, expected_code: str):
    with pytest.raises(AcquisitionError) as exc_info:
        validate_source_uri(uri)
    assert exc_info.value.code == expected_code


# --- Fetch tests with mocked transport ---

def test_fetch_source_success():
    body = b"%PDF-1.4 test"
    transport = _transport_for_response(_pdf_response(body))
    client = httpx.Client(transport=transport)
    result = fetch_source("https://example.com/file.pdf", client=client)
    assert result.status_code == 200
    assert result.content_type == "application/pdf"
    assert result.raw_bytes == body


def test_fetch_source_blocks_http():
    transport = _transport_for_response(_pdf_response())
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError) as exc_info:
        fetch_source("http://example.com/file.pdf", client=client)
    assert exc_info.value.code == "SOURCE_URI_INVALID"


def test_fetch_source_rejects_oversized_content_length():
    response = httpx.Response(
        200,
        content=b"",
        headers={"content-type": "application/pdf", "content-length": "30000000"},
    )
    transport = _transport_for_response(response)
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError) as exc_info:
        fetch_source("https://example.com/file.pdf", client=client)
    assert exc_info.value.code == "SOURCE_TOO_LARGE"


def test_fetch_source_aborts_oversized_stream():
    # 27 MiB exceeds the 25 MiB default limit.
    response = httpx.Response(
        200,
        content=b"x" * 28311552,
        headers={"content-type": "application/pdf"},
    )
    transport = _transport_for_response(response)
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError) as exc_info:
        fetch_source("https://example.com/file.pdf", client=client)
    assert exc_info.value.code == "SOURCE_TOO_LARGE"


def test_fetch_source_follows_valid_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(
                302,
                headers={"location": "https://example.com/b"},
            )
        return _pdf_response()

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    result = fetch_source("https://example.com/a", client=client)
    assert result.status_code == 200
    assert result.redirect_count == 1


def test_fetch_source_blocks_redirect_to_private():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://127.0.0.1/b"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError) as exc_info:
        fetch_source("https://example.com/a", client=client)
    assert exc_info.value.code == "SOURCE_REDIRECT_BLOCKED"


def test_fetch_source_enforces_redirect_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://example.com/a"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError) as exc_info:
        fetch_source("https://example.com/a", client=client)
    assert exc_info.value.code == "SOURCE_TOO_MANY_REDIRECTS"


# --- Orchestration tests ---

@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_success(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    body = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 12 Tf 100 700 Td (Hello PDF) Tj ET\nendstream\nendobj\n5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\ntrailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    transport = _transport_for_response(
        httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/pdf"},
        )
    )
    client = httpx.Client(transport=transport)

    report = run_acquisition_preview("legal", job.job_id, client=client)

    assert report.status == AcquisitionStatus.PARSED
    assert report.parse_status == ParseStatus.PARSED
    assert report.computed_content_hash == hashlib.sha256(body).hexdigest()
    assert report.text_preview
    assert report.raw_artifact_persisted is False
    # Source record observed hash is persisted.
    from app.core.rag_ingestion_store import get_source_record

    updated = get_source_record("legal", record.source_id)
    assert updated.content_hash == report.computed_content_hash


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_matching_hash(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    body = b"known document content"
    record = _legal_record(content_hash=hashlib.sha256(body).hexdigest())
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    transport = _transport_for_response(
        httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/plain"},
        )
    )
    client = httpx.Client(transport=transport)
    report = run_acquisition_preview("legal", job.job_id, client=client)
    assert report.status == AcquisitionStatus.PARSED


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_hash_mismatch(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record(content_hash="mismatch")
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)

    body = b"actual document content"
    transport = _transport_for_response(
        httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/plain"},
        )
    )
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError) as exc_info:
        run_acquisition_preview("legal", job.job_id, client=client)
    assert exc_info.value.code == "CONTENT_HASH_MISMATCH"


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_detects_duplicate(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    body = b"duplicate document content"
    record1 = _legal_record(external_document_id="doc-1")
    save_source_record(record1)
    job1 = _legal_job(record1)
    save_job(job1)
    # First acquisition.
    transport = _transport_for_response(
        httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/plain"},
        )
    )
    client = httpx.Client(transport=transport)
    run_acquisition_preview("legal", job1.job_id, client=client)

    record2 = _legal_record(
        source_uri="https://example.com/other.txt", external_document_id="doc-2"
    )
    save_source_record(record2)
    job2 = _legal_job(record2)
    save_job(job2)
    with pytest.raises(AcquisitionError) as exc_info:
        run_acquisition_preview("legal", job2.job_id, client=client)
    assert exc_info.value.code == "DOCUMENT_DUPLICATE"


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_wrong_domain(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)
    with pytest.raises(AcquisitionError) as exc_info:
        run_acquisition_preview("finance", job.job_id)
    assert exc_info.value.code == "JOB_NOT_ELIGIBLE"


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_rejects_non_dry_run(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)
    with pytest.raises(AcquisitionError) as exc_info:
        run_acquisition_preview("legal", job.job_id, dry_run=False)
    assert exc_info.value.code == "DRY_RUN_REQUIRED"


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_rejects_validation_failed_job(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    from app.models.rag_ingestion import ValidationError as VE

    record = _legal_record(license_review_status=None)
    save_source_record(record)
    job = _legal_job(record, status=JobStatus.VALIDATION_FAILED)
    job.validation_errors = [VE(code="LICENSE_REVIEW_REQUIRED", message="missing")]
    save_job(job)
    with pytest.raises(AcquisitionError) as exc_info:
        run_acquisition_preview("legal", job.job_id)
    assert exc_info.value.code == "JOB_NOT_ELIGIBLE"


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_no_parse(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)
    body = b"%PDF-1.4 test"
    transport = _transport_for_response(
        httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/pdf"},
        )
    )
    client = httpx.Client(transport=transport)
    report = run_acquisition_preview("legal", job.job_id, parse=False, client=client)
    assert report.status == AcquisitionStatus.FETCHED
    assert report.parse_status == ParseStatus.NOT_REQUESTED


@patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK)
def test_run_acquisition_preview_persists_failed_report(_mock, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = _legal_job(record)
    save_job(job)
    transport = _transport_for_response(
        httpx.Response(
            500,
            content=b"error",
            headers={"content-type": "text/plain"},
        )
    )
    client = httpx.Client(transport=transport)
    with pytest.raises(AcquisitionError):
        run_acquisition_preview("legal", job.job_id, client=client)

    get_acquisition_report("legal", job.job_id)
    # The report id is not job_id; just assert a report was saved via list.
    from app.core.rag_ingestion_store import list_acquisition_reports

    all_reports = list_acquisition_reports("legal", job_id=job.job_id)
    assert len(all_reports) == 1
    assert all_reports[0].status == AcquisitionStatus.FAILED
