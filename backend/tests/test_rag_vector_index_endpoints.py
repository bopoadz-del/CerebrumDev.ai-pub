"""Tests for RAG vector-index dry-run endpoints."""

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


def _create_embedding_run(client: TestClient):
    response = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": True, "provider_id": "local_feature_hash_v1"},
    )
    assert response.status_code == 200
    return response.json()["run_id"]


def test_create_vector_index_dry_run_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    run_id = _create_embedding_run(client)
    response = client.post(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/vector-index-dry-run",
        json={"dry_run": True, "adapter_id": "local_flat_json_v1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["adapter_id"] == "local_flat_json_v1"
    assert data["retrieval_enabled"] is False
    assert data["activation_status"] == "inactive"
    assert data["indexed_record_count"] == 1


def test_create_vector_index_dry_run_rejects_non_dry_run(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    run_id = _create_embedding_run(client)
    response = client.post(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/vector-index-dry-run",
        json={"dry_run": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DRY_RUN_REQUIRED"


def test_create_vector_index_dry_run_rejects_unknown_adapter(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    run_id = _create_embedding_run(client)
    response = client.post(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/vector-index-dry-run",
        json={"dry_run": True, "adapter_id": "unknown"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VECTOR_ADAPTER_NOT_FOUND"


def test_list_vector_index_runs_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    run_id = _create_embedding_run(client)
    dry_run_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/vector-index-dry-run",
        json={"dry_run": True, "adapter_id": "local_flat_json_v1"},
    )
    assert dry_run_resp.status_code == 200

    response = client.get("/v1/domains/legal/rag-ingestion/documents/doc1/vector-index-runs")
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "legal"
    assert data["document_id"] == "doc1"
    assert len(data["index_runs"]) == 1
    assert data["index_runs"][0]["status"] == "completed"


def test_get_vector_index_run_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    run_id = _create_embedding_run(client)
    dry_run_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/vector-index-dry-run",
        json={"dry_run": True, "adapter_id": "local_flat_json_v1"},
    )
    index_run_id = dry_run_resp.json()["index_run_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/vector-index-runs/{index_run_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["index_run_id"] == index_run_id
    assert data["status"] == "completed"


def test_list_vector_index_records_endpoint(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch)
    run_id = _create_embedding_run(client)
    dry_run_resp = client.post(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/embedding-runs/{run_id}/vector-index-dry-run",
        json={"dry_run": True, "adapter_id": "local_flat_json_v1"},
    )
    index_run_id = dry_run_resp.json()["index_run_id"]

    response = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/vector-index-runs/{index_run_id}/records"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["records"]) == 1
    assert data["records"][0]["chunk_id"] == "chunk1"
    assert data["records"][0]["vector"] is None

    response_with_vectors = client.get(
        f"/v1/domains/legal/rag-ingestion/documents/doc1/vector-index-runs/{index_run_id}/records",
        params={"include_vectors": True},
    )
    assert response_with_vectors.status_code == 200
    data_with_vectors = response_with_vectors.json()
    assert len(data_with_vectors["records"][0]["vector"]) == data_with_vectors["records"][0]["dimensions"]


def test_vector_index_run_wrong_domain(client: TestClient, tmp_path, monkeypatch):
    _seed_document(client, tmp_path, monkeypatch, domain="legal", document_id="doc1")
    _create_embedding_run(client)
    response = client.get(
        "/v1/domains/medical/rag-ingestion/documents/doc1/vector-index-runs"
    )
    assert response.status_code == 404
