"""H1: a matching golden is OFFERED as a labelled suggestion, never substituted.

The base output of a draft is ALWAYS the user's own draft (input-derived). A
golden match rides on ``blueprint.golden_suggestion`` as an accept/decline
offer (carrying drafting_mode + overlap) whenever its structural overlap clears
the kernel threshold (``golden_match.min_overlap``); it is never swapped in for
the user's draft, and there is no on/off flag.

Two-input test: two different user drafts -> two different BASE outputs, each
the user's own draft and neither the golden; above the threshold the golden is
surfaced as a labelled suggestion on both while the base stays the user's
draft.

MUTANTS this file reddens:
- SUBSTITUTION (the golden replaces the draft) makes the base == golden for both
  inputs, so the "base is the user's draft" asserts in the two-input test red.
- THRESHOLD (always-suggest, ignoring the overlap floor) attaches a suggestion
  to the below-threshold draft, so ``assert ... golden_suggestion is None`` in
  the below-threshold test reds.
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
    return monkeypatch


def _install_fake_inner(monkeypatch, cap_ids):
    """Install a draft inner that emits an input-derived draft with ``cap_ids``.

    The product_id/name come from the brief (so two briefs -> two drafts), and
    the capability ids are chosen by the test to control the structural overlap
    with the golden (above vs below the Jaccard threshold).
    """

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
                {"id": cid, "description": cid, "block_ids": [], "strategy_hint": "GENERATE"}
                for cid in cap_ids
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


def test_two_inputs_two_base_drafts_golden_offered_above_threshold(goldens_on_disk):
    # High overlap: the draft shares the golden's three capability ids, so the
    # Jaccard overlap (== 1.0) clears the 0.5 threshold -> golden is offered.
    _install_fake_inner(goldens_on_disk, ["cap_x", "cap_y", "cap_z"])
    draft = product_architect.draft_blueprint_from_brief

    a = draft("Alpha widget co", vertical_hint="acme_vertical")
    b = draft("Beta gadget ltd", vertical_hint="acme_vertical")

    # Two different user drafts -> two different BASE outputs, each the user's
    # OWN draft (input-derived), and NEITHER is the golden. These are the
    # asserts the SUBSTITUTION mutant reddens.
    assert a.product_id != b.product_id
    assert a.product_id == "alpha-widget-co"
    assert b.product_id == "beta-gadget-ltd"
    assert a.product_id != GOLDEN_YAML["product_id"]
    assert b.product_id != GOLDEN_YAML["product_id"]
    assert a.product_name != GOLDEN_YAML["product_name"]
    # The base's own provenance is NOT 'golden' — it was not substituted.
    assert a.drafting_mode == "keyword_fallback"
    assert b.drafting_mode == "keyword_fallback"

    # The golden is ALWAYS OFFERED (no flag) above the threshold, as a labelled
    # accept/decline suggestion carrying drafting_mode + overlap, while the base
    # stays the user's draft.
    for bp in (a, b):
        sug = bp.golden_suggestion
        assert sug is not None
        assert sug["golden_id"] == GOLDEN_YAML["product_id"]
        assert sug["drafting_mode"] == "golden"
        assert sug["overlap"] >= 0.5
        assert "accept" in sug["label"].lower() and "decline" in sug["label"].lower()


def test_below_threshold_no_suggestion(goldens_on_disk):
    # Low overlap: the draft shares NONE of the golden's capability ids, so only
    # the vertical overlaps -> Jaccard({cap_p,cap_q,acme_vertical},
    # {cap_x,cap_y,cap_z,acme_vertical}) = 1/6 ≈ 0.17 < 0.5 -> below threshold.
    _install_fake_inner(goldens_on_disk, ["cap_p", "cap_q"])
    draft = product_architect.draft_blueprint_from_brief

    c = draft("Gamma tools inc", vertical_hint="acme_vertical")

    # The base is still the user's own draft.
    assert c.product_id == "gamma-tools-inc"
    assert c.drafting_mode == "keyword_fallback"
    # Below the threshold -> NO suggestion. The THRESHOLD mutant (ignore the
    # overlap floor / always-suggest) attaches a suggestion here and reddens
    # this assert.
    assert c.golden_suggestion is None
