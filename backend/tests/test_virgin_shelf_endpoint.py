"""Tests for the /v1/domains/virgin endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_get_virgin_domains_endpoint(client: TestClient, tmp_path: Path):
    """The /v1/domains/virgin endpoint returns shelf editions."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_dir = engine_root / "block_store" / "shelves"
    shelf_dir.mkdir(parents=True)
    shelf = {
        "schema_version": "1.0.0",
        "shelf_id": "virgin_domains",
        "name": "Test Shelf",
        "description": "test",
        "editions": [
            {
                "id": "legal",
                "name": "Legal Virgin Edition",
                "domain": "legal",
                "version": "2.0.0",
                "container_class": "app.containers.legal.LegalContainer",
                "blocks": ["pdf", "ocr", "chat", "image", "legal_v2"],
                "source_kit": "block_store/kits/legal/manifest.json",
                "description": "contract review",
            }
        ],
    }
    (shelf_dir / "virgin_domains.json").write_text(
        json.dumps(shelf), encoding="utf-8"
    )

    with patch("app.routers.domains.list_virgin_domains") as mock_list:
        from app.core.virgin_shelf_loader import list_virgin_domains as real_list

        mock_list.return_value = real_list(engine_root)
        response = client.get("/v1/domains/virgin")

    assert response.status_code == 200
    data = response.json()
    assert data["shelf_id"] == "virgin_domains"
    assert len(data["editions"]) == 1
    assert data["editions"][0]["id"] == "legal"


def test_get_virgin_domains_endpoint_shelf_missing(client: TestClient):
    """The endpoint returns 503 when the shelf cannot be loaded."""
    from app.core.virgin_shelf_loader import VirginShelfLoaderError

    with patch(
        "app.routers.domains.list_virgin_domains",
        side_effect=VirginShelfLoaderError("engine not found"),
    ):
        response = client.get("/v1/domains/virgin")
    assert response.status_code == 503
    assert "engine not found" in response.json()["detail"]
