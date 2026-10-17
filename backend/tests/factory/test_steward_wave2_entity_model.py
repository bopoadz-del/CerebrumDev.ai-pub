"""Wave 2: entity DNA population tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.product_dna.emit import _kit_entity_model, build_dna_documents
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]
STEWARD_BP = ROOT / "blueprints/steward/steward.v1.yaml"


def test_estate_entity_model_kit_has_core_entities():
    # The entity model is the Store kit's, found by the vertical it serves.
    model = _kit_entity_model("estate")
    entity_ids = {e["id"] for e in model["entities"]}
    assert "tenant" in entity_ids
    assert "facility_asset" in entity_ids
    assert "audit_event" in entity_ids
    assert "heal_approval" in entity_ids
    assert len(model["relationships"]) >= 4


def test_steward_generate_emits_populated_entity_model(tmp_path):
    bp = load_blueprint(STEWARD_BP)
    out = tmp_path / "Projectsix"
    ProductGenerator(bp, blocks_root=None).generate(out)
    entities = json.loads((out / "product-dna" / "entity_model.json").read_text(encoding="utf-8"))
    assert len(entities["entities"]) >= 5
    assert len(entities["relationships"]) >= 4


def test_non_estate_vertical_keeps_empty_entity_model():
    bp = load_blueprint(STEWARD_BP).model_copy(update={"vertical": "generic"})
    gen = ProductGenerator(bp, blocks_root=None)
    docs = build_dna_documents(bp, gen.plan)
    assert docs["entity_model.json"]["entities"] == []
