"""Tests for the /v1/domains/source-packs endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_get_source_packs_endpoint(client: TestClient, tmp_path: Path):
    """The /v1/domains/source-packs endpoint returns source pack metadata."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_dir = engine_root / "block_store" / "shelves"
    shelf_dir.mkdir(parents=True)
    shelf = {
        "schema_version": "1.0.0",
        "shelf_id": "source_packs",
        "name": "Test Shelf",
        "description": "test",
        "packs": [
            {
                "id": "legal",
                "domain": "legal",
                "name": "Legal Source Pack",
                "description": "contract review",
                "expert_prompt": "You are a legal expert.",
                "workflow": "test",
                "use_cases": ["review contracts"],
                "example_prompts": ["flag clauses"],
                "expected_inputs": ["contracts"],
                "expected_outputs": ["risk flags"],
                "blocks": ["pdf", "ocr", "chat", "image", "legal_v2"],
            }
        ],
    }
    (shelf_dir / "source_packs.json").write_text(
        json.dumps(shelf), encoding="utf-8"
    )

    with patch("app.routers.domains.list_source_packs") as mock_list:
        from app.core.source_pack_loader import list_source_packs as real_list

        mock_list.return_value = real_list(engine_root)
        response = client.get("/v1/domains/source-packs")

    assert response.status_code == 200
    data = response.json()
    assert data["shelf_id"] == "source_packs"
    assert len(data["packs"]) == 1
    assert data["packs"][0]["id"] == "legal"
    assert "expert_prompt" in data["packs"][0]


def test_get_source_packs_endpoint_shelf_missing(client: TestClient):
    """The endpoint returns 503 when the shelf cannot be loaded."""
    from app.core.source_pack_loader import SourcePackLoaderError

    with patch(
        "app.routers.domains.list_source_packs",
        side_effect=SourcePackLoaderError("engine not found"),
    ):
        response = client.get("/v1/domains/source-packs")
    assert response.status_code == 503
    assert "engine not found" in response.json()["detail"]
