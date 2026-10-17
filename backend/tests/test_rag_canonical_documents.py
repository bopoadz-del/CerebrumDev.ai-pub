"""Tests for canonical document and chunk creation orchestration."""

from __future__ import annotations

import hashlib

import pytest

from app.core.rag_canonical_documents import (
    CanonicalDocumentError,
    create_canonical_document,
)
from app.core.rag_ingestion_store import (
    get_canonical_document,
    get_canonical_text,
    list_canonical_chunks,
    save_canonical_document,
)
from app.models.rag_ingestion import (
    AcquisitionStatus,
    CanonicalizationStatus,
    ChunkingStatus,
    DuplicateStatus,
    IndexStatus,
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


def _report(record: RagSourceRecord, text: str, **kwargs) -> RagAcquisitionReport:
    defaults = {
        "job_id": "job-1",
        "source_id": record.source_id,
        "rag_pack_id": record.rag_pack_id,
        "collection_id": record.collection_id,
        "domain": record.domain,
        "source_uri": record.source_uri,
        "source_class": record.source_class,
        "status": AcquisitionStatus.PARSED,
        "parse_status": ParseStatus.PARSED,
        "computed_content_hash": hashlib.sha256(b"raw").hexdigest(),
        "parser_id": "plaintext",
        "parser_version": None,
        "extracted_text": text,
        "duplicate_status": DuplicateStatus.UNIQUE,
    }
    defaults.update(kwargs)
    return RagAcquisitionReport(**defaults)


def test_create_canonical_document_success():
    record = _source_record()
    job = _job(record)
    text = "Hello, world!\n\nThis is a test."
    report = _report(record, text, job_id=job.job_id)
    document, chunks, canonical_text = create_canonical_document(job, report)

    assert document.canonicalization_status == CanonicalizationStatus.CANONICALIZED
    assert document.chunking_status == ChunkingStatus.CHUNKED
    assert document.index_status == IndexStatus.NOT_INDEXED
    assert document.untrusted_content is True
    assert document.character_count == len(canonical_text)
    assert document.canonical_text_hash == hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    assert len(chunks) > 0


def test_create_canonical_document_without_chunks():
    record = _source_record()
    job = _job(record)
    text = "Hello, world!"
    report = _report(record, text, job_id=job.job_id)
    document, chunks, canonical_text = create_canonical_document(
        job, report, create_chunks=False
    )

    assert document.chunking_status == ChunkingStatus.NOT_REQUESTED
    assert document.chunk_count == 0
    assert chunks == []


def test_create_canonical_document_uses_full_text_not_preview():
    record = _source_record()
    job = _job(record)
    long_text = "word " * 5000
    report = _report(record, long_text, job_id=job.job_id)
    document, chunks, canonical_text = create_canonical_document(job, report)

    assert document.character_count == len(canonical_text)
    assert document.character_count > 10000


def test_empty_canonical_text_fails():
    record = _source_record()
    job = _job(record)
    report = _report(record, "   \n\n  ", job_id=job.job_id)
    with pytest.raises(CanonicalDocumentError) as exc_info:
        create_canonical_document(job, report)
    assert exc_info.value.code == "CANONICAL_TEXT_EMPTY"


def test_non_dry_run_rejected():
    record = _source_record()
    job = _job(record)
    report = _report(record, "text", job_id=job.job_id)
    with pytest.raises(CanonicalDocumentError) as exc_info:
        create_canonical_document(job, report, dry_run=False)
    assert exc_info.value.code == "DRY_RUN_REQUIRED"


def test_validation_failed_job_rejected():
    from app.models.rag_ingestion import ValidationError as VE

    record = _source_record()
    job = _job(record)
    job.validation_errors = [VE(code="ERROR", message="error")]
    report = _report(record, "text", job_id=job.job_id)
    with pytest.raises(CanonicalDocumentError) as exc_info:
        create_canonical_document(job, report)
    assert exc_info.value.code == "ACQUISITION_NOT_ELIGIBLE"


def test_unparsed_acquisition_rejected():
    record = _source_record()
    job = _job(record)
    report = _report(
        record, "text", job_id=job.job_id, status=AcquisitionStatus.FETCHED
    )
    report.parse_status = ParseStatus.NOT_STARTED
    with pytest.raises(CanonicalDocumentError) as exc_info:
        create_canonical_document(job, report)
    assert exc_info.value.code == "ACQUISITION_NOT_ELIGIBLE"


def test_deterministic_document_id():
    record = _source_record()
    job = _job(record)
    text = "Deterministic content."
    report = _report(record, text, job_id=job.job_id)
    doc1, _, _ = create_canonical_document(job, report)
    doc2, _, _ = create_canonical_document(job, report)
    assert doc1.document_id == doc2.document_id


def test_different_collection_different_document_id():
    record = _source_record()
    job = _job(record)
    text = "Same content."
    report = _report(record, text, job_id=job.job_id)
    doc1, _, _ = create_canonical_document(job, report)

    record2 = _source_record(collection_id="prebuilt_legal_other")
    job2 = _job(record2)
    report2 = _report(record2, text, job_id=job2.job_id)
    doc2, _, _ = create_canonical_document(job2, report2)

    assert doc1.document_id != doc2.document_id


def test_truncated_parser_marked():
    record = _source_record()
    job = _job(record)
    text = "Short."
    report = _report(
        record,
        text,
        job_id=job.job_id,
        warnings=["Extracted text exceeded 200000 characters and was truncated."],
        parser_truncated=True,
    )
    document, _, _ = create_canonical_document(job, report)
    assert document.parser_truncated is True


def test_truncation_is_the_typed_flag_not_the_warning_prose():
    """A warning that merely says 'truncated' marks nothing; the parser's
    typed flag is the fact."""
    record = _source_record()
    job = _job(record)
    report = _report(
        record,
        "Short.",
        job_id=job.job_id,
        warnings=["The source said its own table was truncated."],
    )
    document, _, _ = create_canonical_document(job, report)
    assert document.parser_truncated is False


def test_save_and_retrieve_canonical_document(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _source_record()
    job = _job(record)
    text = "Persisted canonical document text."
    report = _report(record, text, job_id=job.job_id)
    document, chunks, canonical_text = create_canonical_document(job, report)
    save_canonical_document(document, canonical_text, chunks)

    retrieved = get_canonical_document("legal", document.document_id)
    assert retrieved is not None
    assert retrieved.document_id == document.document_id

    retrieved_text = get_canonical_text("legal", document.document_id)
    assert retrieved_text == canonical_text

    retrieved_chunks = list_canonical_chunks("legal", document.document_id)
    assert len(retrieved_chunks) == len(chunks)
