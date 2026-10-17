"""Resident Engineer is emitted before platform modules."""

from __future__ import annotations

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]


def test_generate_emits_resident_engineer_first(tmp_path):
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    assert bp.factory_scenario.value == "CREATE_PRODUCT"
    assert "resident_engineer" in bp.ui_modules
    assert "operational_chat" in bp.ui_modules

    out = tmp_path / "steward"
    result = ProductGenerator(bp, blocks_root=None).generate(out)
    assert result["resident_engineer"]["maturity"] == "APPRENTICE"

    re_root = out / "product-agent"
    assert (re_root / "agent_charter.yaml").is_file()
    assert (re_root / "agent_identity.json").is_file()
    assert (re_root / "product_dna" / "block_lockfile.json").is_file()
    assert (re_root / "build_events" / "0001_resident_engineer_created.json").is_file()
    assert (out / "docs" / "store" / "store_manager_manifest.json").is_file()
    assert (out / "docs" / "certification" / "dual_certification.json").is_file()
