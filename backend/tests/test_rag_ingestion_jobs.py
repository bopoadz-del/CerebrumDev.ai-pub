"""Tests for RAG ingestion source-record and job persistence."""

from __future__ import annotations

from datetime import datetime

from app.core.rag_ingestion_store import (
    get_job,
    get_source_record,
    list_jobs,
    list_source_records,
    save_job,
    save_source_record,
)
from app.models.rag_ingestion import (
    DuplicateStatus,
    JobStatus,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
)


def _legal_record() -> RagSourceRecord:
    return RagSourceRecord(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_class=SourceClass.PUBLIC_DOMAIN,
        title="US Copyright Act",
        source_uri="https://example.com/copyright-act",
        publisher="US Government",
        license_name="Public Domain",
        license_uri="https://example.com/license",
        license_review_status="approved",
        authority_rating="high",
        content_hash="hash1",
        external_document_id="doc-1",
    )


def test_save_and_get_source_record(tmp_path, monkeypatch):
    """A persisted source record can be retrieved by id."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    saved = save_source_record(record)
    fetched = get_source_record("legal", saved.source_id)
    assert fetched is not None
    assert fetched.title == "US Copyright Act"
    assert fetched.source_class == SourceClass.PUBLIC_DOMAIN


def test_list_source_records_filtered_by_collection(tmp_path, monkeypatch):
    """list_source_records filters by collection_id when provided."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    save_source_record(_legal_record())
    other = _legal_record()
    other.collection_id = "prebuilt_legal_other"
    other.source_id = "other-id"
    save_source_record(other)

    core_records = list_source_records("legal", collection_id="prebuilt_legal_core")
    assert len(core_records) == 1
    assert core_records[0].collection_id == "prebuilt_legal_core"


def test_save_and_get_job(tmp_path, monkeypatch):
    """A persisted ingestion job can be retrieved by id."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = RagIngestionJob(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_id=record.source_id,
        status=JobStatus.QUEUED,
        dry_run=True,
        duplicate_status=DuplicateStatus.UNIQUE,
        queued_at=datetime.utcnow(),
    )
    saved = save_job(job)
    fetched = get_job("legal", saved.job_id)
    assert fetched is not None
    assert fetched.status == JobStatus.QUEUED
    assert fetched.dry_run is True


def test_list_jobs_with_filters(tmp_path, monkeypatch):
    """list_jobs supports status, rag_pack_id, and collection_id filters."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _legal_record()
    save_source_record(record)
    job = RagIngestionJob(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_id=record.source_id,
        status=JobStatus.QUEUED,
        dry_run=True,
        duplicate_status=DuplicateStatus.UNIQUE,
    )
    save_job(job)

    assert len(list_jobs("legal")) == 1
    assert len(list_jobs("legal", status="queued")) == 1
    assert len(list_jobs("legal", status="validated")) == 0
    assert len(list_jobs("legal", rag_pack_id="legal_core_rag")) == 1
    assert len(list_jobs("legal", collection_id="prebuilt_legal_core")) == 1
    assert len(list_jobs("legal", collection_id="other")) == 0
