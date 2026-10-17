"""Tests for the /v1/domains/rag-packs endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


_SOURCE_POLICY = {
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
}


_STRUCTURED_INGESTION_STATUS = {
    "state": "not_ingested",
    "documents_total": 0,
    "documents_indexed": 0,
    "chunks_total": 0,
    "last_ingested_at": None,
    "last_error": None,
}


def _make_rag_pack(domain: str) -> dict:
    """Return a minimal valid RAG pack for endpoint tests."""
    return {
        "id": f"{domain}_core_rag",
        "domain": domain,
        "name": f"{domain.title()} Core RAG Pack",
        "description": f"{domain} knowledge",
        "collection_id": f"prebuilt_{domain}_core",
        "visibility": "platform_prebuilt",
        "data_class": "public_or_licensed_reference",
        "enterprise_specific": False,
        "requires_blocks": ["knowledge", "vector_search"],
        "recommended_with_blocks": [f"{domain}_v2", "formula_executor_v2"],
        "source_types": ["reference"],
        "expected_queries": [],
        "expected_outputs": [],
        "fetch_mode": "metadata_only",
        "source_policy": _SOURCE_POLICY,
        "ingestion_status": _STRUCTURED_INGESTION_STATUS,
        "notes": [],
    }


def test_get_rag_packs_endpoint(client: TestClient, tmp_path: Path):
    """The /v1/domains/rag-packs endpoint returns RAG pack metadata."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_dir = engine_root / "block_store" / "shelves"
    shelf_dir.mkdir(parents=True)
    shelf = {
        "schema_version": "1.0.0",
        "shelf_id": "rag_packs",
        "name": "Test Shelf",
        "description": "test",
        "packs": [_make_rag_pack("legal"), _make_rag_pack("finance")],
    }
    (shelf_dir / "rag_packs.json").write_text(json.dumps(shelf), encoding="utf-8")

    with patch("app.routers.domains.list_rag_packs") as mock_list:
        from app.core.rag_pack_loader import list_rag_packs as real_list

        mock_list.return_value = real_list(engine_root)
        response = client.get("/v1/domains/rag-packs")

    assert response.status_code == 200
    data = response.json()
    assert data["shelf_id"] == "rag_packs"
    assert len(data["packs"]) == 2
    by_domain = {p["domain"]: p for p in data["packs"]}
    assert {"legal", "finance"} == set(by_domain)
    for p in by_domain.values():
        assert "requires_blocks" in p
        assert "recommended_with_blocks" in p
        assert "knowledge" in p["requires_blocks"]
        assert "source_policy" in p
        assert p["source_policy"]["requires_source_record"] is True
        assert "ingestion_status" in p
        assert p["ingestion_status"]["state"] == "not_ingested"
        assert p["ingestion_status"]["documents_total"] == 0


def test_get_rag_packs_endpoint_shelf_missing(client: TestClient):
    """The endpoint returns 503 when the shelf cannot be loaded."""
    from app.core.rag_pack_loader import RagPackLoaderError

    with patch(
        "app.routers.domains.list_rag_packs",
        side_effect=RagPackLoaderError("engine not found"),
    ):
        response = client.get("/v1/domains/rag-packs")
    assert response.status_code == 503
    assert "engine not found" in response.json()["detail"]
