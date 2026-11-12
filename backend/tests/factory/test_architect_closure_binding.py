"""The kit closure is the architect draft's source of record (advisor 18:18Z).

The model chooses where a closure block goes; it cannot drop one. Two inputs,
same payload: a non-empty closure the payload binds none of, and an empty
closure. Ids are synthetic; no vertical or brief is named by the code.
"""

from __future__ import annotations

from app.factory import store_kits
from app.factory.product_architect import _blueprint_from_llm_payload

_PAYLOAD = {
    "product_name": "Synthetic",
    "capabilities": [
        {"id": "cap_a", "description": "first", "block_ids": []},
        {"id": "cap_b", "description": "second", "block_ids": ["outside_closure"]},
    ],
}
_DUAL = ["k1", "k2", "outside_closure"]


def _draft(monkeypatch, closure: frozenset):
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: closure)
    return _blueprint_from_llm_payload(dict(_PAYLOAD), "a brief naming no block", "v", list(_DUAL))


def test_closure_blocks_the_model_dropped_are_bound_as_reuse(monkeypatch) -> None:
    bp = _draft(monkeypatch, frozenset({"k1", "k2"}))
    bound = {b for cap in bp.capabilities for b in cap.block_ids}
    assert bound == {"k1", "k2"}
    for cap in bp.capabilities:
        if cap.block_ids:
            assert str(getattr(cap.strategy_hint, "value", cap.strategy_hint)) == "REUSE"
    # The model's own capabilities survive; ids outside the closure stay filtered.
    ids = [cap.id for cap in bp.capabilities]
    assert ids[:2] == ["cap_a", "cap_b"]
    assert "outside_closure" not in bound
    assert bp.drafting_note is None


def test_empty_closure_stays_pure_generate_and_says_why(monkeypatch) -> None:
    bp = _draft(monkeypatch, frozenset())
    assert all(not cap.block_ids for cap in bp.capabilities)
    assert [cap.id for cap in bp.capabilities] == ["cap_a", "cap_b"]
    assert bp.drafting_note and "closure empty" in bp.drafting_note


def test_a_closure_block_the_model_named_as_a_capability_binds_there(monkeypatch) -> None:
    payload = {"capabilities": [{"id": "k1", "description": "x", "block_ids": []}]}
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: frozenset({"k1"}))
    bp = _blueprint_from_llm_payload(payload, "b", "v", ["k1"])
    assert [(c.id, list(c.block_ids)) for c in bp.capabilities] == [("k1", ["k1"])]
