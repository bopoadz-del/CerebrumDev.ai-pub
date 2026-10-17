"""Tests for the RAG ingestion validation service."""

from __future__ import annotations

from unittest.mock import patch


from app.core.rag_ingestion_store import save_source_record
from app.core.rag_ingestion_validation import (
    create_ingestion_job,
    validate_source_record,
)
from app.models.rag_ingestion import JobStatus, RagSourceRecord, SourceClass


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


def _valid_legal_record() -> RagSourceRecord:
    return RagSourceRecord(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_class=SourceClass.PUBLIC_DOMAIN,
        title="US Copyright Act",
        source_uri="https://example.com/copyright-act",
        publisher="US Government",
        license_name="Public Domain",
        license_review_status="approved",
        authority_rating="high",
        content_hash="hash1",
        external_document_id="doc-1",
    )


def test_valid_legal_source_passes():
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        result = validate_source_record(_valid_legal_record())
    assert result.valid is True
    assert result.errors == []


def test_unknown_rag_pack_fails():
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=None):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "RAG_PACK_NOT_FOUND" for e in result.errors)


def _get_pack(domain: str):
    if domain == "legal":
        return LEGAL_PACK
    if domain == "finance":
        return {
            "id": "finance_core_rag",
            "domain": "finance",
            "collection_id": "prebuilt_finance_core",
            "source_policy": LEGAL_PACK["source_policy"],
        }
    return None


def test_domain_mismatch_fails():
    record = _valid_legal_record()
    record.domain = "finance"
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", side_effect=_get_pack
    ):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(
        e.code in {"RAG_PACK_NOT_FOUND", "RAG_PACK_ID_MISMATCH"}
        for e in result.errors
    )


def test_collection_id_mismatch_fails():
    record = _valid_legal_record()
    record.collection_id = "wrong"
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "COLLECTION_ID_MISMATCH" for e in result.errors)


def test_precluded_source_class_fails():
    record = _valid_legal_record()
    record.source_class = SourceClass.UNKNOWN_LICENSE
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(
        e.code in {"SOURCE_CLASS_PRECLUDED", "SOURCE_CLASS_NOT_ALLOWED"}
        for e in result.errors
    )


def test_missing_license_review_fails():
    record = _valid_legal_record()
    record.license_review_status = None
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "LICENSE_REVIEW_REQUIRED" for e in result.errors)


def test_unapproved_license_review_fails():
    record = _valid_legal_record()
    record.license_review_status = "pending"
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "LICENSE_REVIEW_NOT_APPROVED" for e in result.errors)


def test_missing_authority_rating_fails():
    record = _valid_legal_record()
    record.authority_rating = None
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "AUTHORITY_RATING_REQUIRED" for e in result.errors)


def test_duplicate_content_hash_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.external_document_id = "doc-2"
        second.source_uri = "https://example.com/other"
        result = validate_source_record(second)
    assert result.valid is False
    assert any(e.code == "DUPLICATE_CONTENT_HASH" for e in result.errors)


def test_duplicate_external_document_id_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.content_hash = "hash2"
        second.source_uri = "https://example.com/other"
        result = validate_source_record(second)
    assert result.valid is False
    assert any(e.code == "DUPLICATE_EXTERNAL_DOCUMENT_ID" for e in result.errors)


def test_duplicate_source_uri_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.content_hash = "hash2"
        second.external_document_id = "doc-2"
        result = validate_source_record(second)
    assert result.valid is False
    assert any(e.code == "DUPLICATE_SOURCE_URI" for e in result.errors)


def test_duplicate_in_different_collection_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    legal_record = _valid_legal_record()
    finance_record = RagSourceRecord(
        rag_pack_id="finance_core_rag",
        collection_id="prebuilt_finance_core",
        domain="finance",
        source_class=SourceClass.PUBLIC_DOMAIN,
        title="Finance Sample",
        source_uri=legal_record.source_uri,
        license_review_status="approved",
        authority_rating="high",
        content_hash=legal_record.content_hash,
        external_document_id=legal_record.external_document_id,
    )
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", side_effect=_get_pack
    ):
        save_source_record(legal_record)
        result = validate_source_record(finance_record)
    assert result.valid is True


def test_dry_run_job_validated():
    record = _valid_legal_record()
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        job = create_ingestion_job(record, dry_run=True, queue=False)
    assert job.status == JobStatus.VALIDATED
    assert job.dry_run is True


def test_dry_run_queue_job_queued():
    record = _valid_legal_record()
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        job = create_ingestion_job(record, dry_run=True, queue=True)
    assert job.status == JobStatus.QUEUED
    assert job.queued_at is not None


def test_validation_failed_job_not_queued():
    record = _valid_legal_record()
    record.license_review_status = None
    with patch(
        "app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK
    ):
        job = create_ingestion_job(record, dry_run=True, queue=True)
    assert job.status == JobStatus.VALIDATION_FAILED
    assert job.queued_at is None
