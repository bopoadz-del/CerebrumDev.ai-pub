"""R1 + R3 (Advisor 12:13Z): Store defects are named by block, and the lock
check accepts only a recorded override that passed its exam."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build import store_override as so


def _block(root: Path, bid: str, body: str = "def run(x):\n    return x\n") -> Path:
    d = root / "vendor" / "blocks" / bid
    d.mkdir(parents=True, exist_ok=True)
    (d / "block.json").write_text('{"id": "%s"}\n' % bid, encoding="utf-8")
    (d / "main.py").write_text(body, encoding="utf-8")
    return d


def _lock(root: Path, *bids: str) -> dict:
    blocks = {}
    for bid in bids:
        path = f"vendor/blocks/{bid}"
        blocks[bid] = {
            "path": path,
            "commit": "c0ffee",
            so.VENDORED_HASH_KEY: so.tree_hash(root / path),
        }
    return {"blocks": blocks}


def _repair(root: Path, bid: str) -> None:
    (root / "vendor" / "blocks" / bid / "main.py").write_text(
        "def run(x):\n    return abs(x)\n", encoding="utf-8"
    )


def _record(root: Path, lock: dict, bid: str, **over) -> so.OverrideRecord:
    fields = dict(
        block_id=bid,
        replaces_hash=lock["blocks"][bid][so.VENDORED_HASH_KEY],
        override_hash=so.tree_hash(root / "vendor" / "blocks" / bid),
        reason="wrong sign on a negative input",
        exam_id="exam-1",
        exam_passed=True,
        store_commit="c0ffee",
    )
    fields.update(over)
    return so.OverrideRecord(**fields)


# --- R1 ---------------------------------------------------------------------


def test_finding_inside_a_vendored_block_is_a_store_defect_of_that_block():
    defects = so.store_defects(
        [
            {"file": "vendor\\blocks\\blk_a\\main.py", "kind": "wrong_answer", "input": {"x": -2}},
            {"path": "./vendor/blocks/blk_b/sub/x.py", "kind": "crash"},
            {"file": "app/handlers.py", "kind": "wrong_answer"},
            {"file": "vendor/blocks", "kind": "crash"},
            {"kind": "crash"},
            "not a mapping",
        ]
    )
    assert defects == [
        so.StoreDefect("blk_a", "vendor/blocks/blk_a/main.py", "wrong_answer", {"x": -2}),
        so.StoreDefect("blk_b", "vendor/blocks/blk_b/sub/x.py", "crash", None),
    ]


def test_store_defects_dedupe_per_block_kind_and_input_and_default_the_kind():
    defects = so.store_defects(
        [
            {"file": "vendor/blocks/blk_a/main.py", "input": 1},
            {"file": "vendor/blocks/blk_a/other.py", "input": 1},
            {"file": "vendor/blocks/blk_a/main.py", "input": 2},
        ]
    )
    assert [(d.kind, d.failing_input) for d in defects] == [("unknown", 1), ("unknown", 2)]


def test_block_of_path_needs_the_vendor_blocks_prefix():
    assert so.block_of_path("vendor/blocks/blk_a/main.py") == "blk_a"
    assert so.block_of_path("app/vendor/blocks/blk_a/main.py") is None
    assert so.block_of_path("vendor/kits/blk_a/main.py") is None
    assert so.block_of_path(None) is None


# --- R3: the lock check -------------------------------------------------------


def test_untouched_vendored_blocks_pass(tmp_path):
    _block(tmp_path, "blk_a")
    _block(tmp_path, "blk_b")
    check = so.verify_vendored(tmp_path, _lock(tmp_path, "blk_a", "blk_b"))
    assert check.ok
    assert check.unchanged == ("blk_a", "blk_b")
    assert check.overridden == () and check.refused == ()


def test_unrecorded_change_to_a_store_block_is_refused(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    check = so.verify_vendored(tmp_path, lock)
    assert not check.ok
    assert check.refused == (
        "blk_a: differs from the vendored Store copy and no override is recorded",
    )


def test_a_planted_file_is_a_change_even_if_untracked(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    (tmp_path / "vendor" / "blocks" / "blk_a" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    assert not so.verify_vendored(tmp_path, lock).ok


def test_pycache_and_line_endings_are_not_changes(tmp_path):
    d = _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00\x01")
    lf = (d / "main.py").read_bytes().replace(b"\r\n", b"\n")
    (d / "main.py").write_bytes(lf.replace(b"\n", b"\r\n"))
    assert so.verify_vendored(tmp_path, lock).ok


def test_recorded_override_that_passed_its_exam_is_accepted(tmp_path):
    _block(tmp_path, "blk_a")
    _block(tmp_path, "blk_b")
    lock = _lock(tmp_path, "blk_a", "blk_b")
    _repair(tmp_path, "blk_a")
    row = so.record_override(lock, _record(tmp_path, lock, "blk_a"))
    assert row["exam_passed"] is True and row["store_commit"] == "c0ffee"
    check = so.verify_vendored(tmp_path, lock)
    assert check.ok
    assert check.overridden == ("blk_a",)
    assert check.unchanged == ("blk_b",)


def test_override_that_drifted_after_its_record_is_refused(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    so.record_override(lock, _record(tmp_path, lock, "blk_a"))
    (tmp_path / "vendor" / "blocks" / "blk_a" / "main.py").write_text("X = 2\n", encoding="utf-8")
    check = so.verify_vendored(tmp_path, lock)
    assert check.refused == (
        "blk_a: differs from both the vendored copy and its recorded override",
    )


def test_override_that_failed_its_exam_is_never_recorded(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    with pytest.raises(so.OverrideError, match="did not pass its exam"):
        so.record_override(lock, _record(tmp_path, lock, "blk_a", exam_passed=False))
    with pytest.raises(so.OverrideError, match="did not pass its exam"):
        so.record_override(lock, _record(tmp_path, lock, "blk_a", exam_id=""))
    assert so.OVERRIDES_KEY not in lock


def test_override_must_name_the_vendored_copy_and_a_vendored_block(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    with pytest.raises(so.OverrideError, match="does not name the vendored copy"):
        so.record_override(lock, _record(tmp_path, lock, "blk_a", replaces_hash="sha256:other"))
    rec = _record(tmp_path, lock, "blk_a")
    with pytest.raises(so.OverrideError, match="no such block"):
        so.record_override(lock, so.OverrideRecord(**{**rec.to_dict(), "block_id": "blk_z"}))


def test_hand_written_override_rows_are_still_checked(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    good = _record(tmp_path, lock, "blk_a").to_dict()

    lock[so.OVERRIDES_KEY] = {"blk_a": {**good, "exam_passed": "yes"}}
    assert so.verify_vendored(tmp_path, lock).refused == (
        "blk_a: recorded override did not pass its exam",
    )
    lock[so.OVERRIDES_KEY] = {"blk_a": {**good, "replaces_hash": "sha256:other"}}
    assert so.verify_vendored(tmp_path, lock).refused == (
        "blk_a: recorded override replaces a different copy than the one vendored",
    )
    lock[so.OVERRIDES_KEY] = {"blk_a": "not a mapping"}
    assert not so.verify_vendored(tmp_path, lock).ok
    lock[so.OVERRIDES_KEY] = {"blk_a": good, "blk_z": good}
    assert so.verify_vendored(tmp_path, lock).refused == (
        "blk_z: override recorded for a block the lock never vendored",
    )


def test_lock_entries_without_a_recorded_hash_or_copy_are_refused(tmp_path):
    _block(tmp_path, "blk_a")
    lock = {
        "blocks": {
            "blk_a": {"path": "vendor/blocks/blk_a"},
            "blk_gone": {"path": "vendor/blocks/blk_gone", so.VENDORED_HASH_KEY: "sha256:x"},
            "kit_no_path": {"commit": "c0ffee"},
            "junk": "not a mapping",
        }
    }
    check = so.verify_vendored(tmp_path, lock)
    assert check.refused == (
        "blk_a: no recorded hash of the vendored copy",
        "blk_gone: vendored copy is missing",
    )
    assert not check.ok


def test_record_round_trips_and_coerces_exam_passed_strictly():
    rec = so.OverrideRecord("b", "h1", "h2", "why", "e", True, "c")
    assert so.OverrideRecord.from_mapping(rec.to_dict()) == rec
    assert so.OverrideRecord.from_mapping({"exam_passed": 1}).exam_passed is False
    assert so.OverrideRecord.from_mapping({}).block_id == ""
