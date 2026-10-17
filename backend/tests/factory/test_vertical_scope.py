"""A block scoped to some verticals is not cloned into the others.

Owner, 2026-09-21, about ``contract_retrieval``, ``drawing_qto`` and
``formula_executor_v2``: "assign them for construction platforms only ...
construction, interior design office, architecture offices and facility
management. only these four".

The Factory holds no list of domains. The words live on the Store's block
manifests; these tests drive the one general rule with a faux Store so they
say nothing about which domains exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import CapabilitySpec
from app.factory.planner import CapabilityPlanner
from app.factory.vertical_scope import block_verticals, normalize_vertical, scope_blocks


def _store(root: Path, manifests: dict) -> Path:
    for bid, extra in manifests.items():
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid, **extra}), encoding="utf-8")
        (d / "block.py").write_text("def run(**k):\n    return {}\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "free_text, slug",
    [
        ("construction", "construction"),
        ("Interior Design Office", "interior_design"),
        ("architecture offices", "architecture"),
        ("Facility-Management", "facility_management"),
        ("interior_design_studio", "interior_design"),
        ("hotel", "hotel"),
        ("", ""),
    ],
)
def test_a_blueprints_free_text_vertical_meets_a_blocks_declared_slug(free_text, slug):
    assert normalize_vertical(free_text) == slug


def test_only_the_organisational_noun_is_stripped_never_a_domain_word():
    """``real_estate`` once lost its second word to a token split and matched
    unrelated prose. Names stay whole: an office is a kind of organisation,
    an estate is not."""
    assert normalize_vertical("real_estate") == "real_estate"
    assert normalize_vertical("office") == "office"  # nothing left to be a suffix OF


def test_a_block_that_declares_nothing_is_eligible_everywhere(tmp_path):
    store = _store(tmp_path, {"plain": {}, "scoped": {"verticals": ["alpha"]}})

    scopes = block_verticals(store)

    assert "plain" not in scopes
    kept, dropped = scope_blocks(["plain"], "anything_at_all", scopes)
    assert kept == ["plain"] and dropped == []


def test_a_scoped_block_is_kept_inside_its_verticals_and_dropped_outside(tmp_path):
    store = _store(tmp_path, {"scoped": {"verticals": ["alpha", "beta_gamma"]}})
    scopes = block_verticals(store)

    assert scope_blocks(["scoped"], "Beta Gamma Offices", scopes)[0] == ["scoped"]

    kept, dropped = scope_blocks(["scoped"], "delta", scopes)
    assert kept == []
    assert dropped[0]["block_id"] == "scoped"
    assert dropped[0]["allowed_verticals"] == ["alpha", "beta_gamma"]
    assert "delta" in dropped[0]["reason"]


def test_a_blueprint_with_no_vertical_does_not_get_a_scoped_block(tmp_path):
    """Absence is not membership. Unscoped blocks are unaffected."""
    scopes = block_verticals(_store(tmp_path, {"scoped": {"verticals": ["alpha"]}}))

    kept, dropped = scope_blocks(["scoped"], "", scopes)

    assert kept == [] and len(dropped) == 1


def test_a_malformed_declaration_scopes_nothing_and_does_not_raise(tmp_path):
    store = _store(tmp_path, {"a": {"verticals": []}, "b": {"verticals": "alpha"}})
    (store / "block_registry" / "c").mkdir()
    (store / "block_registry" / "c" / "block.json").write_text("{not json", encoding="utf-8")

    assert block_verticals(store) == {}


# ── the planner is the chokepoint ──────────────────────────────────────────


def _planner(store: Path) -> CapabilityPlanner:
    planner = CapabilityPlanner.__new__(CapabilityPlanner)
    planner.blocks_root = store
    planner.factory_shelf = None
    planner._dual = {"scoped", "plain"}
    planner._vertical_scopes = None
    return planner


class _Blueprint:
    product_id = "p"

    def __init__(self, vertical, caps):
        self.vertical = vertical
        self.capabilities = caps


def test_the_planner_drops_the_block_and_keeps_the_capability(tmp_path):
    """COLLECTOR reads block ids from the plan and CLONER from COLLECTOR, so
    what the planner drops is never cloned. The capability survives with its
    other block; the build is not refused."""
    store = _store(tmp_path, {"scoped": {"verticals": ["alpha"]}, "plain": {}})
    cap = CapabilitySpec(id="measure", description="measure things", block_ids=["scoped", "plain"])

    planned, unsupported, used = _planner(store)._resolve(_Blueprint("delta", [cap]))

    assert unsupported == []
    assert used == ["plain"]
    assert planned[0].block_ids == ["plain"]
    # the architect's document is not edited under it
    assert cap.block_ids == ["scoped", "plain"]


def test_inside_the_vertical_the_same_blueprint_keeps_the_block(tmp_path):
    store = _store(tmp_path, {"scoped": {"verticals": ["alpha"]}, "plain": {}})
    cap = CapabilitySpec(id="measure", description="measure things", block_ids=["scoped", "plain"])

    _planned, _unsupported, used = _planner(store)._resolve(_Blueprint("Alpha Offices", [cap]))

    assert used == ["scoped", "plain"]


def test_every_drop_is_recorded_with_its_reason(tmp_path):
    """Logged precedence, not a silent edit."""
    store = _store(tmp_path, {"scoped": {"verticals": ["alpha"]}, "plain": {}})
    planner = _planner(store)
    planner._resolve(_Blueprint("delta", [CapabilitySpec(id="measure", description="measure things", block_ids=["scoped"])]))

    row = planner._last_scoped_out[0]
    assert row["capability_id"] == "measure"
    assert row["block_id"] == "scoped"
    assert row["vertical"] == "delta"
    assert "not attached" in row["reason"]


def test_a_capability_left_with_no_blocks_falls_to_generation_not_failure(tmp_path):
    store = _store(tmp_path, {"scoped": {"verticals": ["alpha"]}})
    cap = CapabilitySpec(id="measure", description="measure things", block_ids=["scoped"])

    planned, unsupported, used = _planner(store)._resolve(_Blueprint("delta", [cap]))

    assert unsupported == [] and used == []
    assert planned[0].strategy == "GENERATE"
