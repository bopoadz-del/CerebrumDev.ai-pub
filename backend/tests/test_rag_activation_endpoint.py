"""Tests for the /v1/domains/{domain_id}/rag-activation endpoint."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


LEGAL_ACTIVATION = {
    "domain": "legal",
    "status": "available_metadata_only",
    "attachable": True,
    "attached": False,
    "rag_pack": {
        "id": "legal_core_rag",
        "collection_id": "prebuilt_legal_core",
        "ingestion_status": {
            "state": "not_ingested",
            "documents_total": 0,
            "documents_indexed": 0,
            "chunks_total": 0,
            "last_ingested_at": None,
            "last_error": None,
        },
        "fetch_mode": "metadata_only",
        "enterprise_specific": False,
        "source_policy": {
            "requires_source_record": True,
            "requires_license_review": True,
            "requires_authority_rating": True,
        },
    },
    "requires_blocks": ["knowledge", "vector_search"],
    "recommended_with_blocks": ["legal_v2", "formula_executor_v2"],
    "missing_requirements": [],
    "notes": [
        "Prebuilt RAG pack metadata is available, but no documents or embeddings are ingested yet."
    ],
}

UNKNOWN_ACTIVATION = {
    "domain": "unknown",
    "status": "not_available",
    "attachable": False,
    "attached": False,
    "rag_pack": None,
    "requires_blocks": [],
    "recommended_with_blocks": [],
    "missing_requirements": [],
    "notes": ["No prebuilt RAG pack metadata found for this domain."],
}


def test_get_rag_activation_for_legal(client: TestClient):
    """The endpoint returns activation metadata for a domain with a RAG pack."""
    with patch(
        "app.routers.domains.build_rag_activation_status", return_value=LEGAL_ACTIVATION
    ):
        response = client.get("/v1/domains/legal/rag-activation")

    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "legal"
    assert data["status"] == "available_metadata_only"
    assert data["attachable"] is True
    assert data["attached"] is False
    assert data["rag_pack"]["id"] == "legal_core_rag"
    assert data["rag_pack"]["ingestion_status"]["state"] == "not_ingested"
    assert data["rag_pack"]["source_policy"]["requires_source_record"] is True
    assert "knowledge" in data["requires_blocks"]
    assert "vector_search" in data["requires_blocks"]
    assert "legal_v2" in data["recommended_with_blocks"]


def test_get_rag_activation_for_unknown_domain(client: TestClient):
    """The endpoint returns a clean not-available object for unknown domains."""
    with patch(
        "app.routers.domains.build_rag_activation_status",
        return_value=UNKNOWN_ACTIVATION,
    ):
        response = client.get("/v1/domains/unknown/rag-activation")

    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "unknown"
    assert data["status"] == "not_available"
    assert data["attachable"] is False
    assert data["rag_pack"] is None
