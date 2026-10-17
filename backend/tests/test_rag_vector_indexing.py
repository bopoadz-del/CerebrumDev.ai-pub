import hashlib
from datetime import datetime

import pytest

from app.core.rag_embeddings import run_embedding_dry_run
from app.core.rag_ingestion_store import (
    get_canonical_document,
    save_canonical_document,
    save_embedding_run,
)
from app.core.rag_vector_indexing import (
    VectorIndexError,
    run_vector_index_dry_run,
)
from app.models.rag_ingestion import (
    CanonicalizationStatus,
    ChunkingStatus,
    EmbeddingRunStatus,
    IndexStatus,
    RagCanonicalChunk,
    RagCanonicalDocument,
    VectorIndexRunStatus,
)


def _seed(tmp_path, monkeypatch, text="hello world"):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    domain = "legal"
    collection_id = "prebuilt_legal_core"
    document_id = "doc1"
    doc = RagCanonicalDocument(
        document_id=document_id,
        job_id="job1",
        source_id="source1",
        acquisition_id="acq1",
        rag_pack_id="legal_core_rag",
        collection_id=collection_id,
        domain=domain,
        source_uri="https://example.com/source.pdf",
        title="Test Source",
        source_class="public_domain",
        content_type="text/plain",
        raw_content_hash="abc123",
        canonical_text_hash="def456",
        normalization_algorithm="canonical-text",
        normalization_version="canonical-text-v1",
        parser_id="plain_text",
        parser_version="v1",
        character_count=len(text),
        line_count=1,
        chunk_count=1,
        parser_truncated=False,
        canonicalization_status=CanonicalizationStatus.CANONICALIZED,
        chunking_status=ChunkingStatus.CHUNKED,
        index_status=IndexStatus.NOT_INDEXED,
        untrusted_content=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    chunk = RagCanonicalChunk(
        chunk_id="chunk1",
        document_id=document_id,
        job_id="job1",
        source_id="source1",
        acquisition_id="acq1",
        rag_pack_id="legal_core_rag",
        collection_id=collection_id,
        domain=domain,
        ordinal=0,
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        character_start=0,
        character_end=len(text),
        character_count=len(text),
        overlap_from_previous=0,
        structural_type="fragment",
        chunking_algorithm="structural-character",
        chunking_version="structural-character-v1",
        target_characters=3200,
        maximum_characters=4000,
        overlap_characters=400,
        index_status=IndexStatus.NOT_INDEXED,
        untrusted_content=True,
        created_at=datetime.utcnow(),
    )
    save_canonical_document(doc, text, [chunk])
    run, embeddings = run_embedding_dry_run(doc.domain, doc.document_id)
    return doc, chunk, run, embeddings[0]


def test_run_vector_index_dry_run_success(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    index_run, records = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert index_run.status == VectorIndexRunStatus.COMPLETED
    assert index_run.retrieval_enabled is False
    assert index_run.activation_status.value == "inactive"
    assert index_run.production_approved is False
    assert len(records) == 1
    assert records[0].chunk_id == chunk.chunk_id
    assert records[0].dimensions == 384


def test_run_vector_index_dry_run_idempotent(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    r1, recs1 = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    r2, recs2 = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert r1.index_run_id == r2.index_run_id
    assert r1.index_id == r2.index_id
    assert recs1[0].record_id == recs2[0].record_id


def test_run_vector_index_dry_run_rejects_non_dry_run(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id, dry_run=False)
    assert exc.value.code == "DRY_RUN_REQUIRED"


def test_run_vector_index_dry_run_rejects_unknown_adapter(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id, adapter_id="unknown")
    assert exc.value.code == "VECTOR_ADAPTER_NOT_FOUND"


def test_run_vector_index_dry_run_rejects_failed_embedding_run(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    save_embedding_run(run.model_copy(update={"status": EmbeddingRunStatus.FAILED}), [embedding])
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert exc.value.code == "EMBEDDING_RUN_NOT_ELIGIBLE"


def test_run_vector_index_dry_run_rejects_document_not_canonicalized(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    save_canonical_document(
        doc.model_copy(update={"canonicalization_status": CanonicalizationStatus.FAILED}),
        chunk.text,
        [chunk],
    )
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert exc.value.code == "DOCUMENT_NOT_ELIGIBLE"


def test_run_vector_index_dry_run_rejects_document_not_chunked(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    save_canonical_document(
        doc.model_copy(update={"chunking_status": ChunkingStatus.NOT_REQUESTED}),
        chunk.text,
        [chunk],
    )
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert exc.value.code == "DOCUMENT_NOT_ELIGIBLE"


def test_run_vector_index_dry_run_truncated_document_warns(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    save_canonical_document(
        doc.model_copy(update={"parser_truncated": True}),
        chunk.text,
        [chunk],
    )
    index_run, _ = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert "SOURCE_DOCUMENT_TRUNCATED" in index_run.warnings
    assert "VALIDATION_ONLY_INDEX" in index_run.warnings
    assert "RETRIEVAL_DISABLED" in index_run.warnings


def test_run_vector_index_dry_run_record_metadata_includes_governance(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    index_run, records = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert records[0].metadata["document_id"] == doc.document_id
    assert records[0].metadata["chunk_id"] == chunk.chunk_id
    assert records[0].metadata["collection_id"] == doc.collection_id


def test_run_vector_index_dry_run_state_isolation(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    doc_after = get_canonical_document(doc.domain, doc.document_id)
    assert doc_after.index_status == IndexStatus.NOT_INDEXED


def test_index_validation_errors_fail_the_run(tmp_path, monkeypatch):
    """adapter.validate_index errors must fail the index run, not be ignored."""
    from app.core import rag_vector_store_adapters as adapters

    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)

    def broken_validate(self, index_spec):
        return ["records artifact corrupted"]

    monkeypatch.setattr(
        adapters._LocalFlatJsonAdapterV1, "validate_index", broken_validate
    )
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert exc.value.code == "VECTOR_INDEX_VALIDATION_FAILED"
