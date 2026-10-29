"""H1: a matching golden is OFFERED as a labelled suggestion, never substituted.

The base output of a draft is ALWAYS the user's own draft (input-derived). A
golden match rides on ``blueprint.golden_suggestion`` as an accept/decline
offer (carrying drafting_mode + overlap) when ``use_goldens`` is on; it is
never swapped in for the user's draft. ``use_goldens`` defaults OFF.

Two-input test: two different user drafts -> two different BASE outputs, each
the user's own draft and neither the golden; with use_goldens ON the golden is
surfaced as a labelled suggestion while the base stays the user's draft; with
use_goldens OFF (default) no golden is surfaced.

MUTANT: restoring the substitution (the golden replaces the draft) makes the
base == golden for both inputs, so the two "base is the user's draft" asserts
below redden.
"""

from __future__ import annotations

import re

import pytest
import yaml

from app.factory import product_architect
from app.factory.blueprint import ProductBlueprint

GOLDEN_YAML = {
    "schema_version": "product_blueprint.v1",
    "product_id": "acme-golden",
    "product_name": "Acme Golden Platform",
    "vertical": "acme_vertical",
    "serves_verticals": ["acme_vertical"],
    "summary": "A golden reference draft, store-backed.",
    "factory_scenario": "CREATE_PRODUCT",
    "capabilities": [
        {"id": "cap_x", "description": "X", "block_ids": ["database"], "strategy_hint": "REUSE"},
        {"id": "cap_y", "description": "Y", "block_ids": ["database"], "strategy_hint": "REUSE"},
        {"id": "cap_z", "description": "Z", "block_ids": ["database"], "strategy_hint": "REUSE"},
    ],
    "ui_modules": ["command_center"],
    "connectors": [],
}


@pytest.fixture
def goldens_on_disk(tmp_path, monkeypatch):
    bp_dir = tmp_path / "blueprints"
    bp_dir.mkdir()
    (bp_dir / "acme.yaml").write_text(yaml.safe_dump(GOLDEN_YAML), encoding="utf-8")
    monkeypatch.setattr(product_architect, "_repo_root", lambda: tmp_path)

    # Offline + deterministic: no Store clone, so the golden scores on its
    # own structure alone (no kit capabilities folded in).
    import app.factory.blocks_source as blocks_source

    def _no_store():
        raise RuntimeError("no Store clone in this test")

    monkeypatch.setattr(blocks_source, "resolve_blocks_root", _no_store)

    # The user's own draft: input-derived and structurally matching the golden
    # (so the golden is eligible to be offered), but with its OWN identity.
    def fake_inner(brief, *, vertical_hint=None, use_llm=None):
        slug = re.sub(r"[^a-z0-9]+", "-", brief.lower()).strip("-") or "draft"
        raw = {
            "schema_version": "product_blueprint.v1",
            "product_id": slug,
            "product_name": brief.strip().title(),
            "vertical": "acme_vertical",
            "summary": brief.strip(),
            "factory_scenario": "CREATE_PRODUCT",
            "capabilities": [
                {"id": "cap_x", "description": "x", "block_ids": [], "strategy_hint": "GENERATE"},
                {"id": "cap_y", "description": "y", "block_ids": [], "strategy_hint": "GENERATE"},
                {"id": "cap_z", "description": "z", "block_ids": [], "strategy_hint": "GENERATE"},
            ],
            "ui_modules": ["command_center"],
            "connectors": [],
            "edge_profile": "standard",
            "human_authority": True,
            "drafting_mode": "keyword_fallback",
        }
        return ProductBlueprint.model_validate(raw)

    monkeypatch.setattr(
        product_architect, "_draft_blueprint_from_brief_inner", fake_inner
    )
    return tmp_path


def test_two_inputs_two_base_drafts_golden_only_offered(goldens_on_disk):
    draft = product_architect.draft_blueprint_from_brief

    a = draft("Alpha widget co", vertical_hint="acme_vertical", use_goldens=True)
    b = draft("Beta gadget ltd", vertical_hint="acme_vertical", use_goldens=True)

    # Two different user drafts -> two different BASE outputs, each the user's
    # OWN draft (input-derived), and NEITHER is the golden. These are the
    # asserts the substitution mutant reddens.
    assert a.product_id != b.product_id
    assert a.product_id == "alpha-widget-co"
    assert b.product_id == "beta-gadget-ltd"
    assert a.product_id != GOLDEN_YAML["product_id"]
    assert b.product_id != GOLDEN_YAML["product_id"]
    assert a.product_name != GOLDEN_YAML["product_name"]
    # The base's own provenance is NOT 'golden' — it was not substituted.
    assert a.drafting_mode == "keyword_fallback"
    assert b.drafting_mode == "keyword_fallback"

    # The golden is OFFERED as a labelled accept/decline suggestion carrying
    # drafting_mode + overlap, while the base stays the user's draft.
    for bp in (a, b):
        sug = bp.golden_suggestion
        assert sug is not None
        assert sug["golden_id"] == GOLDEN_YAML["product_id"]
        assert sug["drafting_mode"] == "golden"
        assert sug["overlap"] >= 0.5
        assert "accept" in sug["label"].lower() and "decline" in sug["label"].lower()


def test_use_goldens_defaults_off_no_golden_surfaced(goldens_on_disk):
    draft = product_architect.draft_blueprint_from_brief
    # Default (use_goldens omitted) surfaces NO golden at all.
    c = draft("Alpha widget co", vertical_hint="acme_vertical")
    assert c.golden_suggestion is None
    assert c.product_id == "alpha-widget-co"
    assert c.drafting_mode == "keyword_fallback"
