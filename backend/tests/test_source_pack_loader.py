"""Tests for the Cerebrum-Blocks source pack loader (read-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.source_pack_loader import (
    SourcePackLoaderError,
    get_source_pack,
    list_source_pack_domain_ids,
    list_source_packs,
    load_source_packs,
)


@pytest.fixture
def fake_engine_with_source_packs(tmp_path: Path) -> Path:
    """Build a minimal fake engine checkout containing a source pack shelf."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_dir = engine_root / "block_store" / "shelves"
    shelf_dir.mkdir(parents=True)

    shelf = {
        "schema_version": "1.0.0",
        "shelf_id": "source_packs",
        "name": "Test Source Pack Shelf",
        "description": "test",
        "packs": [
            {
                "id": "legal",
                "domain": "legal",
                "name": "Legal Source Pack",
                "description": "contract review",
                "expert_prompt": "You are a legal expert.",
                "workflow": "1) ingest 2) analyse 3) chat",
                "use_cases": ["review contracts"],
                "example_prompts": ["flag risky clauses"],
                "expected_inputs": ["contracts"],
                "expected_outputs": ["risk flags"],
                "blocks": ["pdf", "ocr", "chat", "image", "legal_v2"],
            },
            {
                "id": "finance",
                "domain": "finance",
                "name": "Finance Source Pack",
                "description": "financial docs",
                "expert_prompt": "You are a finance expert.",
                "workflow": "1) ingest 2) analyse 3) chat",
                "use_cases": ["analyse earnings"],
                "example_prompts": ["flag revenue decline"],
                "expected_inputs": ["financial statements"],
                "expected_outputs": ["revenue flags"],
                "blocks": ["pdf", "ocr", "chat", "image", "finance_v2"],
            },
        ],
    }
    (shelf_dir / "source_packs.json").write_text(
        json.dumps(shelf), encoding="utf-8"
    )
    return engine_root


def test_load_source_packs(fake_engine_with_source_packs: Path):
    data = load_source_packs(fake_engine_with_source_packs)
    assert data["shelf_id"] == "source_packs"
    assert len(data["packs"]) == 2


def test_load_source_packs_missing_file(tmp_path: Path):
    with pytest.raises(SourcePackLoaderError):
        load_source_packs(tmp_path)


def test_load_source_packs_invalid_json(tmp_path: Path):
    engine_root = tmp_path / "Cerebrum-Blocks"
    shelf_path = engine_root / "block_store" / "shelves" / "source_packs.json"
    shelf_path.parent.mkdir(parents=True)
    shelf_path.write_text("not json", encoding="utf-8")
    with pytest.raises(SourcePackLoaderError):
        load_source_packs(engine_root)


def test_list_source_packs(fake_engine_with_source_packs: Path):
    packs = list_source_packs(fake_engine_with_source_packs)
    assert len(packs) == 2
    ids = {p["id"] for p in packs}
    assert ids == {"legal", "finance"}
    for p in packs:
        assert "expert_prompt" in p
        assert "workflow" in p
        assert "use_cases" in p
        assert "example_prompts" in p
        assert "expected_inputs" in p
        assert "expected_outputs" in p
        assert "blocks" in p


def test_list_source_pack_domain_ids(fake_engine_with_source_packs: Path):
    assert list_source_pack_domain_ids(fake_engine_with_source_packs) == ["finance", "legal"]


def test_get_source_pack(fake_engine_with_source_packs: Path):
    legal = get_source_pack("legal", fake_engine_with_source_packs)
    assert legal is not None
    assert legal["domain"] == "legal"
    assert "legal_v2" in legal["blocks"]
    assert get_source_pack("missing", fake_engine_with_source_packs) is None
