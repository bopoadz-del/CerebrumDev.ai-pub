"""Tests for RAG embedding dry-run endpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi.testclient import TestClient

from app.core.rag_ingestion_store import save_canonical_document
from app.models.rag_ingestion import (
    CanonicalizationStatus,
    ChunkingStatus,
    IndexStatus,
    RagCanonicalChunk,
    RagCanonicalDocument,
)


def _seed_document(client: TestClient, tmp_path, monkeypatch, domain="legal", document_id="doc1"):
    """Persist a canonical document with one chunk for endpoint tests."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    collection_id = "prebuilt_legal_core"
    text = "hello world"
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


def test_create_embedding_dry_run_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    response = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": True, "provider_id": "local_feature_hash_v1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["provider_id"] == "local_feature_hash_v1"
    assert data["document_chunk_count"] == 1
    assert data["embedded_chunk_count"] == 1
    assert data["failed_chunk_count"] == 0


def test_create_embedding_dry_run_rejects_non_dry_run(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    response = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DRY_RUN_REQUIRED"


def test_get_embedding_runs_wrong_domain(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch, domain="legal", document_id="doc1")
    response = client.get(
        "/v1/domains/medical/rag-ingestion/documents/doc1/embedding-runs"
    )
    assert response.status_code == 404


def test_list_embedding_runs_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    dry_run_resp = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": True, "provider_id": "local_feature_hash_v1"},
    )
    assert dry_run_resp.status_code == 200

    response = client.get("/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["document_id"] == "doc1"
    assert data[0]["status"] == "completed"


def test_get_embedding_run_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    dry_run_resp = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": True, "provider_id": "local_feature_hash_v1"},
    )
    run_id = dry_run_resp.json()["run_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert data["status"] == "completed"


def test_list_chunk_embeddings_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    dry_run_resp = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": True, "provider_id": "local_feature_hash_v1"},
    )
    run_id = dry_run_resp.json()["run_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/embeddings"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["chunk_id"] == "chunk1"
    assert data[0]["vector"] is None

    response_with_vectors = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/embeddings",
        params={"include_vectors": True},
    )
    assert response_with_vectors.status_code == 200
    data_with_vectors = response_with_vectors.json()
    assert len(data_with_vectors[0]["vector"]) == data_with_vectors[0]["dimensions"]
