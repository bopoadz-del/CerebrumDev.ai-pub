"""Tests for the Cerebrum-Blocks virgin shelf loader (read-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.virgin_shelf_loader import (
    VirginShelfLoaderError,
    get_virgin_domain,
    is_virgin_domain,
    list_virgin_domains,
    load_virgin_shelf,
    virgin_domain_ids,
)


@pytest.fixture
def fake_engine_with_shelf(tmp_path: Path) -> Path:
    """Build a minimal fake engine checkout containing a virgin shelf."""
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
            },
            {
                "id": "finance",
                "name": "Finance Virgin Edition",
                "domain": "finance",
                "version": "1.0.0",
                "container_class": "app.containers.finance.FinanceContainer",
                "blocks": ["pdf", "ocr", "chat", "image", "finance_v2"],
                "source_kit": "block_store/kits/finance/manifest.json",
                "description": "financial docs",
            },
        ],
    }
    (shelf_dir / "virgin_domains.json").write_text(
        json.dumps(shelf), encoding="utf-8"
    )
    return engine_root


def test_load_virgin_shelf(fake_engine_with_shelf: Path):
    data = load_virgin_shelf(fake_engine_with_shelf)
    assert data["shelf_id"] == "virgin_domains"
    assert len(data["editions"]) == 2


def test_load_virgin_shelf_missing_file(tmp_path: Path):
    with pytest.raises(VirginShelfLoaderError):
        load_virgin_shelf(tmp_path)


def test_load_virgin_shelf_invalid_json(tmp_path: Path):
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_path = engine_root / "block_store" / "shelves" / "virgin_domains.json"
    shelf_path.parent.mkdir(parents=True)
    shelf_path.write_text("not json", encoding="utf-8")
    with pytest.raises(VirginShelfLoaderError):
        load_virgin_shelf(engine_root)


def test_list_virgin_domains(fake_engine_with_shelf: Path):
    domains = list_virgin_domains(fake_engine_with_shelf)
    assert len(domains) == 2
    ids = {d["id"] for d in domains}
    assert ids == {"legal", "finance"}
    for d in domains:
        assert "container_class" in d
        assert "blocks" in d
        assert "description" in d


def test_virgin_domain_ids(fake_engine_with_shelf: Path):
    assert virgin_domain_ids(fake_engine_with_shelf) == ["finance", "legal"]


def test_get_virgin_domain(fake_engine_with_shelf: Path):
    legal = get_virgin_domain("legal", fake_engine_with_shelf)
    assert legal is not None
    assert legal["domain"] == "legal"
    assert "legal_v2" in legal["blocks"]
    assert get_virgin_domain("missing", fake_engine_with_shelf) is None


def test_is_virgin_domain(fake_engine_with_shelf: Path):
    assert is_virgin_domain("legal", fake_engine_with_shelf) is True
    assert is_virgin_domain("missing", fake_engine_with_shelf) is False
