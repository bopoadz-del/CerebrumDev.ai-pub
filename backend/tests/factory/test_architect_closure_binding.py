"""The kit closure is the architect draft's source of record (advisor 18:18Z).

The model chooses where a closure block goes; it cannot drop one. Two inputs
per test: closures the payload binds none of, an empty closure, and the
runner's timeline note with and without a drafting note. Ids are synthetic;
no vertical or brief is named by the code.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory import store_kits
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import RoleRunner
from app.factory.product_architect import _blueprint_from_llm_payload

_PAYLOAD = {
    "product_name": "Synthetic",
    "capabilities": [
        {"id": "cap_a", "description": "first", "block_ids": []},
        {"id": "cap_b", "description": "second", "block_ids": ["outside_closure"]},
    ],
}
_ONE_CAP = {"capabilities": [{"id": "cap_z", "description": "only", "block_ids": ["outside_closure"]}]}
_DUAL = ["k1", "k2", "k3", "outside_closure"]


def _draft(monkeypatch, closure: frozenset, payload: dict | None = None):
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: closure)
    return _blueprint_from_llm_payload(dict(payload or _PAYLOAD), "a brief naming no block", "v", list(_DUAL))


@pytest.mark.parametrize("closure", [frozenset({"k1", "k2"}), frozenset({"k3"})])
def test_closure_blocks_the_model_dropped_are_bound_as_reuse(monkeypatch, closure: frozenset) -> None:
    bp = _draft(monkeypatch, closure)
    bound = {b for cap in bp.capabilities for b in cap.block_ids}
    assert bound == set(closure)
    for cap in bp.capabilities:
        if cap.block_ids:
            assert str(getattr(cap.strategy_hint, "value", cap.strategy_hint)) == "REUSE"
    # The model's own capabilities survive; ids outside the closure stay filtered.
    ids = [cap.id for cap in bp.capabilities]
    assert ids[:2] == ["cap_a", "cap_b"]
    assert "outside_closure" not in bound
    assert bp.drafting_note is None


@pytest.mark.parametrize(
    "payload, want_ids",
    [(_PAYLOAD, ["cap_a", "cap_b"]), (_ONE_CAP, ["cap_z"])],
)
def test_empty_closure_stays_pure_generate_and_says_why(monkeypatch, payload: dict, want_ids: list) -> None:
    bp = _draft(monkeypatch, frozenset(), payload)
    assert all(not cap.block_ids for cap in bp.capabilities)
    assert [cap.id for cap in bp.capabilities] == want_ids
    assert bp.drafting_note and "closure empty" in bp.drafting_note


@pytest.mark.parametrize("bid", ["k1", "k2"])
def test_a_closure_block_the_model_named_as_a_capability_binds_there(monkeypatch, bid: str) -> None:
    payload = {"capabilities": [{"id": bid, "description": "x", "block_ids": []}]}
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: frozenset({bid}))
    bp = _blueprint_from_llm_payload(payload, "b", "v", [bid])
    assert [(c.id, list(c.block_ids)) for c in bp.capabilities] == [(bid, [bid])]


@pytest.mark.parametrize(
    "note, want_details",
    [("closure empty: synthetic", ["architect: closure empty: synthetic"]), ("", [])],
)
def test_note_drafting_note_reaches_the_timeline(tmp_path: Path, note: str, want_details: list) -> None:
    """_note_drafting_note writes one COLLECTOR NOTE only when the blueprint
    carries a note. constant_return writes nothing for a real note; drop_rule
    writes an empty `architect: ` note when there is none."""
    ledger = BuildLedger(tmp_path / "l.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ns = SimpleNamespace(ledger=ledger, blueprint=SimpleNamespace(drafting_note=note))
    RoleRunner._note_drafting_note(ns)

    notes = [
        e for e in ledger.events()
        if e.kind is EventKind.NOTE and "drafting_note" in (e.payload or {})
    ]
    assert [e.detail for e in notes] == want_details
    assert all(e.role is BuildRole.COLLECTOR for e in notes)
