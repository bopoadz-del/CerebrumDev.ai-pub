"""Kit-resolution determinism: identical pin -> identical closure.

The live-pilot failure shape (ORDERS 14:42Z) was the brief->kit resolution
resolving DIFFERENT block sets for the SAME brief + Store pin because an LLM
picked the kit. ``store_kits.kit_closure`` is the deterministic resolver: it
reads the pinned Store's own kit + governance declarations, so the same pin
and vertical always yield the same block closure. ``product_architect`` then
filters any LLM-proposed blocks to that closure, so the draft never inherits
the model's free pick.

These tests feed the closure two synthetic Store pins (two inputs) and prove
each pin's closure is stable, the two pins differ exactly as their declared
kit content differs, and the architect only keeps blocks inside the closure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.store_kits import GOVERNANCE, NO_VERTICAL, kit_closure


def _manifest(root: Path, kit_id: str, serves: list, blocks: list) -> None:
    kit_dir = root / "block_store" / "kits" / kit_id
    kit_dir.mkdir(parents=True, exist_ok=True)
    (kit_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": kit_id,
                "status": "available",
                "build_ready": True,
                "serves_verticals": serves,
                "blocks": blocks,
            }
        ),
        encoding="utf-8",
    )


def _block(root: Path, block_id: str, capability_class: str | None = None) -> None:
    block_dir = root / "block_registry" / block_id
    block_dir.mkdir(parents=True, exist_ok=True)
    data = {"id": block_id}
    if capability_class:
        data["capability_class"] = capability_class
    (block_dir / "block.json").write_text(json.dumps(data), encoding="utf-8")


def _store(tmp_path: Path, kit_id: str, vertical: str, blocks: list) -> Path:
    """A synthetic Store pin: one serving kit + one governance block."""
    root = tmp_path / kit_id
    _manifest(root, kit_id, [vertical], blocks)
    for bid in blocks:
        _block(root, bid)
    _block(root, "audit", capability_class=GOVERNANCE)
    return root


#: Two distinct Store pins (two inputs): each declares a different kit content.
PINS = [
    ("construction", ["pdf", "ocr", "boq_processor", "drawing_qto"]),
    ("finance_ops", ["pdf", "ocr", "finance_reconciliation"]),
]


@pytest.mark.parametrize(
    ("vertical", "blocks", "expected"),
    [
        (
            "construction",
            ["pdf", "ocr", "boq_processor", "drawing_qto"],
            frozenset({"pdf", "ocr", "boq_processor", "drawing_qto", "audit"}),
        ),
        (
            "finance_ops",
            ["pdf", "ocr", "finance_reconciliation"],
            frozenset({"pdf", "ocr", "finance_reconciliation", "audit"}),
        ),
    ],
)
def test_identical_pin_gives_identical_closure(
    tmp_path: Path, vertical: str, blocks: list, expected: frozenset
) -> None:
    """Same pin + same vertical, resolved twice, is the same closure."""
    pin = _store(tmp_path, vertical, vertical, blocks)
    first = kit_closure(vertical, pin)
    second = kit_closure(vertical, pin)
    assert first == second
    assert first == expected


@pytest.mark.parametrize(
    ("vertical", "blocks", "own", "foreign"),
    [
        ("construction", ["pdf", "ocr", "boq_processor", "drawing_qto"], "boq_processor", "finance_reconciliation"),
        ("finance_ops", ["pdf", "ocr", "finance_reconciliation"], "finance_reconciliation", "boq_processor"),
    ],
)
def test_two_pins_have_distinct_closures(
    tmp_path: Path, vertical: str, blocks: list, own: str, foreign: str
) -> None:
    """Each pin's closure carries exactly its own kit blocks, never another's."""
    pin = _store(tmp_path, vertical, vertical, blocks)
    closure = kit_closure(vertical, pin)
    assert own in closure
    assert foreign not in closure


@pytest.mark.parametrize("vertical", ["construction", "finance_ops"])
def test_no_vertical_closure_is_governance_only(tmp_path: Path, vertical: str) -> None:
    """The "no choice, no kit" rule: without a typed vertical the closure is
    the governance blocks only, never an LLM's guess at a domain kit."""
    pin = _store(tmp_path, vertical, vertical, ["pdf"])
    assert kit_closure(NO_VERTICAL, pin) == frozenset({"audit"})


@pytest.mark.parametrize(
    ("kit_blocks", "brief", "dual", "expected"),
    [
        (
            frozenset({"kit_pdf", "audit"}),
            "needs the drawing_qto block",
            {"kit_pdf", "audit", "drawing_qto"},
            frozenset({"kit_pdf", "audit", "drawing_qto"}),
        ),
        (
            frozenset({"kit_pdf"}),
            "no names here",
            {"kit_pdf", "foreign_block"},
            frozenset({"kit_pdf"}),
        ),
    ],
)
def test_deterministic_closure_filters_to_kit_and_brief(
    monkeypatch, kit_blocks: frozenset, brief: str, dual: set, expected: frozenset
) -> None:
    """The architect's allowed set is kit closure + brief-named blocks,
    intersected with the dual registry -- a block the model proposes but the
    closure does not include (and the brief does not name) is dropped."""
    from app.factory import store_kits
    from app.factory.product_architect import _deterministic_closure

    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical: frozenset(kit_blocks))
    assert _deterministic_closure("construction", brief, dual) == expected


def test_blueprint_from_llm_payload_rejects_a_non_object() -> None:
    """A non-object payload raises before the closure filter runs, so a
    malformed model output can never become a draft."""
    from app.factory.product_architect import _blueprint_from_llm_payload

    with pytest.raises(ValueError, match="LLM draft is not a JSON object"):
        _blueprint_from_llm_payload(["not", "a", "dict"], "brief", None, [])
