"""F-3c (a), advisor 21:36Z: 'not delivered' reaches the build's API record.

Read-only surfacing of what the ledger already says. The runner writes one
Store-defect NOTE per block (``BlockRepair.note``); build-status lists each
block whose NEWEST such note names capabilities not delivered. Nothing here
changes how a build runs.
"""

from __future__ import annotations

from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.store_override import REPAIR_FAILED_REASON
from app.factory.build.store_repair import BlockRepair, not_delivered
from app.factory.build_jobs import build_status


def _failed(block_id: str, caps: tuple) -> BlockRepair:
    return BlockRepair(
        block_id=block_id,
        kinds=("crash",),
        attempted=True,
        repaired=False,
        not_delivered=caps,
        reason=REPAIR_FAILED_REASON,
    )


def _repaired(block_id: str) -> BlockRepair:
    return BlockRepair(block_id=block_id, kinds=("crash",), attempted=True, repaired=True)


def _ledger(tmp_path) -> BuildLedger:
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    return ledger


def test_a_failed_repair_reaches_the_build_record(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(EventKind.NOTE, detail="STORE DEFECT blk_a", payload=_failed("blk_a", ("cap_one", "cap_two")).note(1))

    status = build_status(tmp_path)

    assert status["not_delivered"] == [
        {"block_id": "blk_a", "capabilities": ["cap_one", "cap_two"], "reason": REPAIR_FAILED_REASON}
    ]


def test_a_clean_build_lists_nothing(tmp_path):
    _ledger(tmp_path)
    assert build_status(tmp_path)["not_delivered"] == []


def test_a_product_with_no_ledger_is_unknown_and_lists_nothing(tmp_path):
    status = build_status(tmp_path)
    assert status["state"] == "unknown"
    assert "not_delivered" not in status


def test_the_newest_note_per_block_decides(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(EventKind.NOTE, payload=_failed("blk_a", ("cap_one",)).note(1))
    ledger.append(EventKind.NOTE, payload=_repaired("blk_a").note(2))
    ledger.append(EventKind.NOTE, payload=_repaired("blk_b").note(1))
    ledger.append(EventKind.NOTE, payload=_failed("blk_b", ("cap_two",)).note(2))

    rows = build_status(tmp_path)["not_delivered"]

    assert [r["block_id"] for r in rows] == ["blk_b"]
    assert rows[0]["capabilities"] == ["cap_two"]


def test_only_store_defect_notes_are_read():
    class _E:
        def __init__(self, payload):
            self.payload = payload

    # A note that is not a Store-defect note is never read as one, even if
    # it carries a field of the same name.
    events = [_E({"not_delivered": ["cap_x"], "reason": "other"}), _E(None)]
    assert not_delivered(events) == []
