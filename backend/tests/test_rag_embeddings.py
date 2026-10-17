import hashlib
from datetime import datetime

import pytest

from app.core.rag_embeddings import run_embedding_dry_run
from app.core.rag_ingestion_store import (
    get_chunk_embeddings,
    get_embedding_run,
    list_embedding_runs,
    save_canonical_document,
)
from app.models.rag_ingestion import (
    CanonicalizationStatus,
    ChunkingStatus,
    IndexStatus,
    RagCanonicalChunk,
    RagCanonicalDocument,
)


def _make_document(tmp_path, monkeypatch, text="hello world"):
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
    return doc, chunk


def test_run_embedding_dry_run_success(tmp_path, monkeypatch):
    doc, chunk = _make_document(tmp_path, monkeypatch)
    run, embeddings = run_embedding_dry_run(doc.domain, doc.document_id)
    assert run.status.value == "completed"
    assert run.dry_run is True
    assert run.production_approved is False
    assert len(embeddings) == 1
    assert embeddings[0].chunk_id == chunk.chunk_id
    assert embeddings[0].dimensions == 384
    assert len(embeddings[0].vector) == 384


def test_run_embedding_dry_run_idempotent(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    run1, embeddings1 = run_embedding_dry_run(doc.domain, doc.document_id)
    run2, embeddings2 = run_embedding_dry_run(doc.domain, doc.document_id)
    assert run1.run_id == run2.run_id
    assert embeddings1[0].embedding_id == embeddings2[0].embedding_id
    assert embeddings1[0].vector == embeddings2[0].vector


def test_run_embedding_dry_run_rejects_non_dry_run(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    from app.core.rag_embeddings import EmbeddingError
    with pytest.raises(EmbeddingError) as exc:
        run_embedding_dry_run(doc.domain, doc.document_id, dry_run=False)
    assert exc.value.code == "DRY_RUN_REQUIRED"


def test_run_embedding_dry_run_rejects_unknown_provider(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    from app.core.rag_embeddings import EmbeddingError
    with pytest.raises(EmbeddingError) as exc:
        run_embedding_dry_run(doc.domain, doc.document_id, provider_id="unknown")
    assert exc.value.code == "EMBEDDING_PROVIDER_NOT_FOUND"


def test_run_embedding_dry_run_rejects_no_chunks(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    # overwrite with zero chunks
    save_canonical_document(doc.model_copy(update={"chunk_count": 0}), "hello world", [])
    from app.core.rag_embeddings import EmbeddingError
    with pytest.raises(EmbeddingError) as exc:
        run_embedding_dry_run(doc.domain, doc.document_id)
    assert exc.value.code == "DOCUMENT_HAS_NO_CHUNKS"


def test_run_embedding_dry_run_truncated_document_warns(tmp_path, monkeypatch):
    doc, chunk = _make_document(tmp_path, monkeypatch)
    save_canonical_document(
        doc.model_copy(update={"parser_truncated": True}), "hello world", [chunk]
    )
    run, _ = run_embedding_dry_run(doc.domain, doc.document_id)
    assert "SOURCE_DOCUMENT_TRUNCATED" in run.warnings
    assert "VALIDATION_ONLY_PROVIDER" in run.warnings


def test_save_and_retrieve_embedding_run(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    run, embeddings = run_embedding_dry_run(doc.domain, doc.document_id)
    assert get_embedding_run(doc.domain, doc.document_id, run.run_id) is not None
    assert len(list_embedding_runs(doc.domain, doc.document_id)) == 1
    assert len(get_chunk_embeddings(doc.domain, doc.document_id, run.run_id, include_vectors=True)) == 1
    assert len(get_chunk_embeddings(doc.domain, doc.document_id, run.run_id, include_vectors=False)[0].vector) == 0
