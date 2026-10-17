"""A brief reaches a golden by the STRUCTURE of its draft, never by its words."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.factory.golden_match import (
    Golden,
    best_golden,
    draft_structure,
    goldens,
    jaccard,
)

ROOT = Path(__file__).resolve().parents[3]


def _g(name, *ids):
    return Golden(path=Path(f"{name}.yaml"), name=name, structure=frozenset(ids))


class _Cap:
    def __init__(self, cid):
        self.id = cid


class _Draft:
    def __init__(self, vertical, *caps, summary=""):
        self.vertical = vertical
        self.capabilities = [_Cap(c) for c in caps]
        self.summary = summary


def test_best_overlap_above_threshold_wins():
    yard = _g("Zorblat Yard", "gate_in", "gate_out", "yard_map", "zorblat_yards")
    farm = _g("Quillon Farm", "herd", "milking", "quillon_farms")
    draft = draft_structure(_Draft("zorblat_yards", "gate_in", "gate_out", "yard_map"))
    match = best_golden(draft, [yard, farm], threshold=0.5)
    assert match is not None and match[0] is yard and match[1] == 1.0


def test_below_threshold_builds_from_the_draft():
    yard = _g("Zorblat Yard", "gate_in", "gate_out", "yard_map", "zorblat_yards")
    draft = draft_structure(_Draft("zorblat_yards", "zorblat_yards_core", "audit"))
    assert jaccard(draft, yard.structure) < 0.5
    assert best_golden(draft, [yard], threshold=0.5) is None


def test_a_tie_is_not_a_decision():
    a = _g("A", "x", "y")
    b = _g("B", "x", "y")
    assert best_golden(frozenset({"x", "y"}), [a, b], threshold=0.1) is None


def test_the_threshold_is_config(monkeypatch):
    yard = _g("Zorblat Yard", "gate_in", "gate_out", "yard_map", "zorblat_yards")
    draft = draft_structure(_Draft("zorblat_yards", "gate_in"))  # 2 of 4 ids
    monkeypatch.setenv("FACTORY_GOLDEN_MIN_OVERLAP", "0.9")
    assert best_golden(draft, [yard]) is None
    monkeypatch.setenv("FACTORY_GOLDEN_MIN_OVERLAP", "0.4")
    assert best_golden(draft, [yard]) is not None


def test_words_in_the_brief_change_nothing():
    """Two drafts with the same structure and opposite prose route the same."""
    yard = _g("Zorblat Yard", "gate_in", "gate_out", "zorblat_yards")
    plain = _Draft("zorblat_yards", "gate_in", "gate_out", summary="a yard")
    noisy = _Draft("zorblat_yards", "gate_in", "gate_out",
                   summary="residential lettings private estate steward desk")
    assert draft_structure(plain) == draft_structure(noisy)
    assert best_golden(draft_structure(plain), [yard]) == best_golden(draft_structure(noisy), [yard])


def test_a_blueprint_opts_in_by_declaring_what_it_serves(tmp_path):
    def write(name, serves):
        doc = {
            "schema_version": "product_blueprint.v1", "product_id": name,
            "product_name": name.title(), "vertical": name, "summary": "s",
            "capabilities": [{"id": f"{name}_cap", "description": "d", "block_ids": ["database"]}],
        }
        if serves:
            doc["serves_verticals"] = serves
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")

    write("zorblat", ["zorblat"])
    write("plainbp", [])
    found = goldens(tmp_path, store_root=tmp_path)
    assert [g.name for g in found] == ["Zorblat"]
    assert {"zorblat_cap", "zorblat"} <= found[0].structure


def test_the_real_goldens_route_by_their_own_structure():
    """A draft carrying a real golden's capability ids reaches that golden;
    the same vertical with none of its structure does not."""
    from app.factory.blueprint import load_blueprint
    from app.factory.product_architect import _golden_for_draft

    real = goldens(ROOT / "blueprints")
    assert real, "no golden blueprint on disk declares serves_verticals"
    target = load_blueprint(real[0].path)
    # The user's choice is one of the verticals the golden itself declares
    # (owner 2026-10-05: a golden never hands a product a vertical the user
    # did not pick -- no choice, no golden).
    choice = target.serves_verticals[0]
    assert _golden_for_draft(target) is None
    hit = _golden_for_draft(target, choice)
    assert hit is not None and hit.drafting_mode == "golden"
    assert hit.product_id == target.product_id

    thin = target.model_copy(update={"capabilities": target.capabilities[:1]})
    thin_caps = {c.id for c in thin.capabilities}
    assert len(thin_caps) == 1
    assert _golden_for_draft(thin, choice) is None or pytest.fail(
        "one capability of a large golden must not clear the default threshold")
