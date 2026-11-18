"""F-3c (a), advisor 21:36Z: 'not delivered' reaches the build's API record.

Read-only surfacing of what the ledger already says. The runner writes one
Store-defect NOTE per block (``BlockRepair.note``); build-status lists each
block whose NEWEST such note names capabilities not delivered. Nothing here
changes how a build runs.
"""

from __future__ import annotations

import pytest

from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.store_override import REPAIR_FAILED_REASON
from app.factory.build.store_repair import ALLOWANCE_SPENT, BlockRepair, not_delivered
from app.factory.build_jobs import build_status


def _failed(block_id: str, caps: tuple, reason: str = REPAIR_FAILED_REASON) -> BlockRepair:
    return BlockRepair(
        block_id=block_id,
        kinds=("crash",),
        attempted=reason == REPAIR_FAILED_REASON,
        repaired=False,
        not_delivered=caps,
        reason=reason,
    )


def _repaired(block_id: str) -> BlockRepair:
    return BlockRepair(block_id=block_id, kinds=("crash",), attempted=True, repaired=True)


def _ledger(tmp_path) -> BuildLedger:
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    return ledger


@pytest.mark.parametrize(
    "block_id, caps, reason",
    [
        ("blk_a", ("cap_one", "cap_two"), REPAIR_FAILED_REASON),
        ("blk_b", ("cap_three",), ALLOWANCE_SPENT),
    ],
)
def test_a_block_left_unrepaired_reaches_the_build_record(tmp_path, block_id, caps, reason):
    ledger = _ledger(tmp_path)
    ledger.append(EventKind.NOTE, detail=f"STORE DEFECT {block_id}", payload=_failed(block_id, caps, reason).note(1))

    status = build_status(tmp_path)

    assert status["not_delivered"] == [{"block_id": block_id, "capabilities": list(caps), "reason": reason}]


@pytest.mark.parametrize("repaired_blocks", [(), ("blk_a", "blk_b")])
def test_a_build_with_nothing_undelivered_lists_nothing(tmp_path, repaired_blocks):
    ledger = _ledger(tmp_path)
    for block_id in repaired_blocks:
        ledger.append(EventKind.NOTE, payload=_repaired(block_id).note(1))
    assert build_status(tmp_path)["not_delivered"] == []


@pytest.mark.parametrize("product_dir", ["empty", "nested/empty"])
def test_a_product_with_no_ledger_is_unknown_and_lists_nothing(tmp_path, product_dir):
    out = tmp_path / product_dir
    out.mkdir(parents=True)
    status = build_status(out)
    assert status["state"] == "unknown"
    assert "not_delivered" not in status


@pytest.mark.parametrize(
    "order, listed",
    [
        # failed, then repaired: the repair is the newest word -> not listed
        (("failed", "repaired"), []),
        # repaired, then failed: the failure is the newest word -> listed
        (("repaired", "failed"), ["blk_a"]),
    ],
)
def test_the_newest_note_per_block_decides(tmp_path, order, listed):
    ledger = _ledger(tmp_path)
    for n, step in enumerate(order, start=1):
        repair = _failed("blk_a", ("cap_one",)) if step == "failed" else _repaired("blk_a")
        ledger.append(EventKind.NOTE, payload=repair.note(n))

    rows = build_status(tmp_path)["not_delivered"]

    assert [r["block_id"] for r in rows] == listed


class _E:
    def __init__(self, payload):
        self.payload = payload


@pytest.mark.parametrize(
    "payload",
    [
        # Not a Store-defect note, though it carries a field of the same name.
        {"not_delivered": ["cap_x"], "reason": "other"},
        {"not_delivered": ["cap_y"], "store_defect": ""},
    ],
)
def test_only_store_defect_notes_are_read(payload):
    assert not_delivered([_E(payload), _E(None)]) == []
