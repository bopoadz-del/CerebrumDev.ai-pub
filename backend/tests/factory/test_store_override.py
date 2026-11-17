"""R1 + R3 (Advisor 12:13Z): Store defects are named by block, and the lock
check accepts only a recorded override that passed its exam."""

from __future__ import annotations

import json
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
    """The product lock CLONER wrote, and the Factory's signed record of it."""
    blocks = {}
    for bid in bids:
        path = f"vendor/blocks/{bid}"
        blocks[bid] = {
            "path": path,
            "commit": "c0ffee",
            so.VENDORED_HASH_KEY: so.tree_hash(root / path),
        }
    _cloned(root, {b: e[so.VENDORED_HASH_KEY] for b, e in blocks.items()})
    return {"blocks": blocks}


def _cloned(root: Path, hashes: dict) -> None:
    rows = {b: {"commit": "c0ffee", "path": f"vendor/blocks/{b}"} for b in hashes}
    so.record_vendored(_proofs(root), hashes, rows=rows)


def _repair(root: Path, bid: str) -> None:
    (root / "vendor" / "blocks" / bid / "main.py").write_text(
        "def run(x):\n    return abs(x)\n", encoding="utf-8"
    )


#: The Factory's signing key in these tests (the real one is never in a test).
_KEY = b"factory-test-key"
_BUILD = "build-1"


def _proofs(root: Path, build_id: str = _BUILD, key: bytes = _KEY) -> so.ProofStore:
    """The build's Factory-held proof store, outside the product (placement
    itself is tested below with open_proof_store)."""
    return so.ProofStore(root=root.parent / (root.name + "-proofs"), build_id=build_id, key=key)


def _prove(root: Path, bid: str, exam_id: str = "exam-1", **over) -> None:
    """The Factory's proof that ``bid`` passed ``exam_id`` on its bytes now."""
    fields = dict(
        exam_id=exam_id,
        block_id=bid,
        examined_hash=so.tree_hash(root / "vendor" / "blocks" / bid),
        passed=True,
        score=1.0,
    )
    fields.update(over)
    so.write_proof(_proofs(root), so.ExamProof(**fields))


def _verify(root: Path, lock: dict) -> so.OverrideCheck:
    return so.verify_vendored(root, lock, proofs=_proofs(root))


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


_DEFECT_A = so.StoreDefect("blk_a", "vendor/blocks/blk_a/main.py", "wrong_answer", {"x": -2})
_DEFECT_B = so.StoreDefect("blk_b", "vendor/blocks/blk_b/sub/x.py", "crash", None)


@pytest.mark.parametrize(
    "finding, expected",
    [
        (
            {"file": "vendor\\blocks\\blk_a\\main.py", "kind": "wrong_answer", "input": {"x": -2}},
            [_DEFECT_A],
        ),
        ({"path": "./vendor/blocks/blk_b/sub/x.py", "kind": "crash"}, [_DEFECT_B]),
        ({"file": "app/handlers.py", "kind": "wrong_answer"}, []),
        ({"file": "vendor/blocks", "kind": "crash"}, []),
        ({"kind": "crash"}, []),
        ("not a mapping", []),
    ],
)
def test_finding_inside_a_vendored_block_is_a_store_defect_of_that_block(finding, expected):
    assert so.store_defects([finding]) == expected
    # A finding outside any vendored block never hides or adds a defect.
    assert so.store_defects([{"file": "app/x.py", "kind": "crash"}, finding]) == expected


@pytest.mark.parametrize(
    "findings, expected",
    [
        (
            [
                {"file": "vendor/blocks/blk_a/main.py", "input": 1},
                {"file": "vendor/blocks/blk_a/other.py", "input": 1},
                {"file": "vendor/blocks/blk_a/main.py", "input": 2},
            ],
            [("blk_a", "unknown", 1), ("blk_a", "unknown", 2)],
        ),
        (
            [
                {"file": "vendor/blocks/blk_a/main.py", "kind": "crash", "input": 1},
                {"file": "vendor/blocks/blk_a/main.py", "kind": "wrong_answer", "input": 1},
                {"file": "vendor/blocks/blk_b/main.py", "kind": "crash", "input": 1},
                {"file": "vendor/blocks/blk_a/x.py", "kind": "crash", "input": 1},
            ],
            [("blk_a", "crash", 1), ("blk_a", "wrong_answer", 1), ("blk_b", "crash", 1)],
        ),
    ],
)
def test_store_defects_dedupe_per_block_kind_and_input_and_default_the_kind(findings, expected):
    defects = so.store_defects(findings)
    assert [(d.block_id, d.kind, d.failing_input) for d in defects] == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("vendor/blocks/blk_a/main.py", "blk_a"),
        ("vendor\\blocks\\blk_b\\x.py", "blk_b"),
        ("app/vendor/blocks/blk_a/main.py", None),
        ("vendor/kits/blk_a/main.py", None),
        ("vendor/blocks", None),
        (None, None),
    ],
)
def test_block_of_path_needs_the_vendor_blocks_prefix(path, expected):
    assert so.block_of_path(path) == expected


# --- R3: the lock check -------------------------------------------------------


@pytest.mark.parametrize("bids", [("blk_a",), ("blk_a", "blk_b"), ("blk_c", "blk_a", "blk_b")])
def test_untouched_vendored_blocks_pass(tmp_path, bids):
    for bid in bids:
        _block(tmp_path, bid)
    check = _verify(tmp_path, _lock(tmp_path, *bids))
    assert check.ok
    assert check.unchanged == tuple(sorted(bids))
    assert check.overridden == () and check.refused == ()


def _plant(root: Path, bid: str) -> None:
    (root / "vendor" / "blocks" / bid / "extra.py").write_text("X = 1\n", encoding="utf-8")


@pytest.mark.parametrize("change", [_repair, _plant], ids=["edited", "planted"])
def test_unrecorded_change_to_a_store_block_is_refused(tmp_path, change):
    _block(tmp_path, "blk_a")
    _block(tmp_path, "blk_b")
    lock = _lock(tmp_path, "blk_a", "blk_b")
    change(tmp_path, "blk_a")
    check = _verify(tmp_path, lock)
    assert not check.ok
    assert check.unchanged == ("blk_b",)
    assert check.refused == (
        "blk_a: differs from the vendored Store copy and no override is recorded",
    )


def test_a_planted_file_is_a_change_even_if_untracked(tmp_path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    (tmp_path / "vendor" / "blocks" / "blk_a" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    assert not _verify(tmp_path, lock).ok


def test_pycache_and_line_endings_are_not_changes(tmp_path):
    d = _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00\x01")
    lf = (d / "main.py").read_bytes().replace(b"\r\n", b"\n")
    (d / "main.py").write_bytes(lf.replace(b"\n", b"\r\n"))
    assert _verify(tmp_path, lock).ok


@pytest.mark.parametrize("repaired, untouched", [("blk_a", "blk_b"), ("blk_b", "blk_a")])
def test_recorded_override_that_passed_its_exam_is_accepted(tmp_path, repaired, untouched):
    _block(tmp_path, "blk_a")
    _block(tmp_path, "blk_b")
    lock = _lock(tmp_path, "blk_a", "blk_b")
    _repair(tmp_path, repaired)
    _prove(tmp_path, repaired)
    row = so.record_override(lock, _record(tmp_path, lock, repaired))
    assert row["exam_passed"] is True and row["store_commit"] == "c0ffee"
    assert row["block_id"] == repaired
    check = _verify(tmp_path, lock)
    assert check.ok
    assert check.overridden == (repaired,)
    assert check.unchanged == (untouched,)


def _rewrite(root: Path, bid: str) -> None:
    (root / "vendor" / "blocks" / bid / "main.py").write_text("X = 2\n", encoding="utf-8")


@pytest.mark.parametrize("drift", [_rewrite, _plant], ids=["rewritten", "planted"])
def test_override_that_drifted_after_its_record_is_refused(tmp_path, drift):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    _prove(tmp_path, "blk_a")
    so.record_override(lock, _record(tmp_path, lock, "blk_a"))
    drift(tmp_path, "blk_a")
    check = _verify(tmp_path, lock)
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


@pytest.mark.parametrize(
    "overrides, expected",
    [
        (
            lambda good: {"blk_a": {**good, "exam_passed": "yes"}},
            ("blk_a: recorded override did not pass its exam",),
        ),
        (
            lambda good: {"blk_a": {**good, "replaces_hash": "sha256:other"}},
            ("blk_a: recorded override replaces a different copy than the one vendored",),
        ),
        (
            lambda good: {"blk_a": "not a mapping"},
            ("blk_a: differs from the vendored Store copy and no override is recorded",),
        ),
        (
            lambda good: {"blk_a": good, "blk_z": good},
            ("blk_z: override recorded for a block the lock never vendored",),
        ),
    ],
    ids=["exam-not-bool", "other-copy", "not-a-mapping", "unvendored-block"],
)
def test_hand_written_override_rows_are_still_checked(tmp_path, overrides, expected):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    _prove(tmp_path, "blk_a")
    lock[so.OVERRIDES_KEY] = overrides(_record(tmp_path, lock, "blk_a").to_dict())
    check = _verify(tmp_path, lock)
    assert check.refused == expected
    assert not check.ok


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"path": "vendor/blocks/blk_a", "commit": "c0ffee"}, ()),
        (
            {"path": "vendor/blocks/blk_gone", so.VENDORED_HASH_KEY: "sha256:x"},
            ("blk_a: not vendored at vendor/blocks/blk_a",),
        ),
        ({"commit": "c0ffee"}, ("blk_a: the lock's row no longer says where it is vendored",)),
        ("not a mapping", ("blk_a: the lock's row no longer says where it is vendored",)),
    ],
    ids=["no-hash-claim", "other-path", "no-path", "not-a-mapping"],
)
def test_lock_entries_without_a_recorded_hash_or_copy_are_refused(tmp_path, entry, expected):
    _block(tmp_path, "blk_a")
    _lock(tmp_path, "blk_a")
    check = _verify(tmp_path, {"blocks": {"blk_a": entry}})
    assert check.refused == expected
    assert check.ok is (not expected)
    assert check.unchanged == (() if expected else ("blk_a",))


@pytest.mark.parametrize(
    "record",
    [
        so.OverrideRecord("b", "h1", "h2", "why", "e", True, "c"),
        so.OverrideRecord("b2", "h3", "h4", "other", "e2", False),
    ],
)
def test_record_round_trips(record):
    assert so.OverrideRecord.from_mapping(record.to_dict()) == record


@pytest.mark.parametrize(
    "raw, field, expected",
    [
        ({"exam_passed": 1}, "exam_passed", False),
        ({"exam_passed": "true"}, "exam_passed", False),
        ({"exam_passed": True}, "exam_passed", True),
        ({}, "block_id", ""),
        ({"block_id": None}, "block_id", ""),
    ],
)
def test_record_coerces_exam_passed_strictly(raw, field, expected):
    assert getattr(so.OverrideRecord.from_mapping(raw), field) == expected


# --- F-2: the exam's proof is held by the Factory, outside the product --------


@pytest.mark.parametrize("bid", ["blk_a", "blk_q"])
def test_a_missing_vendored_copy_is_refused(tmp_path, bid):
    _cloned(tmp_path, {bid: "sha256:x"})
    entry = {"path": f"vendor/blocks/{bid}", "commit": "c0ffee", so.VENDORED_HASH_KEY: "sha256:x"}
    check = _verify(tmp_path, {"blocks": {bid: entry}})
    assert check.refused == (f"{bid}: vendored copy is missing",)


@pytest.mark.parametrize("path", ["vendor/blocks/other", "src/blk_a", "vendor/blocks/blk_a/sub"])
def test_a_block_vendored_anywhere_but_its_own_path_is_refused(tmp_path, path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    lock["blocks"]["blk_a"]["path"] = path
    check = _verify(tmp_path, lock)
    assert check.refused == ("blk_a: not vendored at vendor/blocks/blk_a",)


@pytest.mark.parametrize("path", ["./vendor/blocks/blk_a", r"vendor\blocks\blk_a/"])
def test_spellings_of_the_vendored_path_are_the_same_path(tmp_path, path):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    lock["blocks"]["blk_a"]["path"] = path
    assert _verify(tmp_path, lock).unchanged == ("blk_a",)


def _no_proof(root: Path) -> None:
    pass


def _failed_proof(root: Path) -> None:
    _prove(root, "blk_a", passed=False)


def _other_block_proof(root: Path) -> None:
    _prove(root, "blk_a", block_id="blk_b")


def _other_tree_proof(root: Path) -> None:
    _prove(root, "blk_a", examined_hash="sha256:not-these-bytes")


@pytest.mark.parametrize(
    "prove, expected",
    [
        (_no_proof, "blk_a: no Factory-held proof of its exam"),
        (_failed_proof, "blk_a: the Factory-held proof does not show it passing its exam"),
        (_other_block_proof, "blk_a: the Factory-held proof does not show it passing its exam"),
        (_other_tree_proof, "blk_a: these bytes are not the tree its exam examined"),
    ],
    ids=["no-proof", "failed", "other-block", "other-tree"],
)
def test_an_override_row_the_factory_holds_no_passing_proof_for_is_refused(
    tmp_path, prove, expected
):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    prove(tmp_path)
    lock[so.OVERRIDES_KEY] = {"blk_a": _record(tmp_path, lock, "blk_a").to_dict()}
    check = _verify(tmp_path, lock)
    assert check.refused == (expected,) and not check.ok


@pytest.mark.parametrize("exam_id", ["", None])
def test_an_override_row_with_no_exam_id_is_refused(tmp_path, exam_id):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    _repair(tmp_path, "blk_a")
    so._write_signed(
        _proofs(tmp_path),
        so._proof_name(""),
        so.EXAM_KIND,
        {
            "exam_id": "",
            "block_id": "blk_a",
            "passed": True,
            "examined_hash": so.tree_hash(tmp_path / "vendor" / "blocks" / "blk_a"),
        },
    )
    row = {**_record(tmp_path, lock, "blk_a").to_dict(), "exam_id": exam_id}
    lock[so.OVERRIDES_KEY] = {"blk_a": row}
    assert _verify(tmp_path, lock).refused == ("blk_a: recorded override names no exam",)


@pytest.mark.parametrize("inside", [".", "docs/proofs"])
def test_proofs_held_inside_the_product_are_refused(tmp_path, inside):
    _block(tmp_path, "blk_a")
    inner = so.ProofStore(root=tmp_path / inside, build_id=_BUILD, key=_KEY)
    check = so.verify_vendored(tmp_path, _lock(tmp_path, "blk_a"), proofs=inner)
    assert check.refused == ("exam proofs are held inside the product tree",)
    assert not check.ok and check.unchanged == ()


@pytest.mark.parametrize(
    "proof",
    [
        so.ExamProof("exam:a:1", "blk_a", "sha256:1", True, 1.0),
        so.ExamProof("exam:b:2", "blk_b", "sha256:2", False, 0.25),
    ],
)
def test_a_proof_reads_back_as_written_and_leaves_no_temp_file(tmp_path, proof):
    store = so.ProofStore(root=tmp_path, build_id=_BUILD, key=_KEY)
    path = so.write_proof(store, proof)
    assert so.read_proof(store, proof.exam_id) == proof
    assert path.name.endswith(so.PROOF_SUFFIX)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


@pytest.mark.parametrize("block_id", ["blk_a", "blk_b"])
def test_a_proof_without_an_exam_id_is_never_written(tmp_path, block_id):
    with pytest.raises(so.OverrideError, match="has no exam id"):
        so.write_proof(
            so.ProofStore(root=tmp_path, build_id=_BUILD, key=_KEY),
            so.ExamProof("", block_id, "sha256:1", True),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "text",
    ["{torn", "[1, 2]", '{"exam_id": "exam:other", "passed": true}'],
    ids=["not-json", "not-an-object", "other-exam"],
)
def test_an_unreadable_or_foreign_proof_is_no_proof(tmp_path, text):
    store = so.ProofStore(root=tmp_path, build_id=_BUILD, key=_KEY)
    (tmp_path / so._proof_name("exam:a:1")).write_text(text, encoding="utf-8")
    assert so.read_proof(store, "exam:a:1") is None


@pytest.mark.parametrize("score, read", [(1, 1.0), ("high", None)])
def test_a_proof_score_is_a_number_or_nothing(tmp_path, score, read):
    store = so.ProofStore(root=tmp_path, build_id=_BUILD, key=_KEY)
    so._write_signed(
        store,
        so._proof_name("exam:a:1"),
        so.EXAM_KIND,
        {"exam_id": "exam:a:1", "passed": "yes", "score": score},
    )
    proof = so.read_proof(store, "exam:a:1")
    assert proof.score == read and proof.passed is False and proof.block_id == ""


@pytest.mark.parametrize(
    "inner, inside",
    [("a", True), ("a/b/c", True), ("a/b/../c", True), (".", False), ("a-proofs", False)],
)
def test_is_inside_reads_the_tree_not_the_name(tmp_path, inner, inside):
    assert so.is_inside(tmp_path / inner, tmp_path / "a") is inside


@pytest.mark.parametrize("block_id", ["blk_a", "blk_b"])
def test_an_empty_exam_id_reads_no_proof_even_if_a_file_claims_it(tmp_path, block_id):
    store = so.ProofStore(root=tmp_path, build_id=_BUILD, key=_KEY)
    so._write_signed(
        store, so._proof_name(""), so.EXAM_KIND, {"exam_id": "", "block_id": block_id, "passed": True}
    )
    assert so.read_proof(store, "") is None


# --- F-2 (grader 19:35Z): signed, build-bound, out of the agent's reach -------


@pytest.mark.parametrize("bid", ["blk_a", "blk_q"])
def test_a_proof_signed_with_another_key_is_no_proof(tmp_path, bid):
    _block(tmp_path, bid)
    lock = _lock(tmp_path, bid)
    _repair(tmp_path, bid)
    forged = _proofs(tmp_path, key=b"a-key-the-writer-made-up")
    so.write_proof(
        forged,
        so.ExamProof("exam-1", bid, so.tree_hash(tmp_path / "vendor" / "blocks" / bid), True, 1.0),
    )
    lock[so.OVERRIDES_KEY] = {bid: _record(tmp_path, lock, bid).to_dict()}
    assert _verify(tmp_path, lock).refused == (f"{bid}: no Factory-held proof of its exam",)


@pytest.mark.parametrize("bid", ["blk_a", "blk_q"])
def test_a_proof_from_another_build_is_no_proof(tmp_path, bid):
    _block(tmp_path, bid)
    lock = _lock(tmp_path, bid)
    _repair(tmp_path, bid)
    other = _proofs(tmp_path, build_id="build-2")
    so.write_proof(
        other,
        so.ExamProof("exam-1", bid, so.tree_hash(tmp_path / "vendor" / "blocks" / bid), True, 1.0),
    )
    lock[so.OVERRIDES_KEY] = {bid: _record(tmp_path, lock, bid).to_dict()}
    assert _verify(tmp_path, lock).refused == (f"{bid}: no Factory-held proof of its exam",)


@pytest.mark.parametrize("field, value", [("passed", True), ("examined_hash", "sha256:other")])
def test_an_edited_signed_proof_is_no_proof(tmp_path, field, value):
    store = _proofs(tmp_path)
    path = so.write_proof(store, so.ExamProof("exam-1", "blk_a", "sha256:1", False, 0.0))
    body = json.loads(path.read_text(encoding="utf-8"))
    body["payload"][field] = value
    path.write_text(json.dumps(body), encoding="utf-8")
    assert so.read_proof(store, "exam-1") is None


def test_a_vendored_record_is_not_an_exam_proof_and_back(tmp_path):
    store = _proofs(tmp_path)
    so._write_signed(store, so._proof_name("exam-1"), so.VENDORED_KIND, {"exam_id": "exam-1"})
    so._write_signed(store, so.VENDORED_RECORD, so.EXAM_KIND, {"blocks": {"blk_a": "h"}})
    assert so.read_proof(store, "exam-1") is None
    assert so.vendored_record(store) is None


@pytest.mark.parametrize("bid", ["blk_a", "blk_q"])
def test_no_proof_store_refuses_the_lock_check(tmp_path, bid):
    _block(tmp_path, bid)
    lock = _lock(tmp_path, bid)
    check = so.verify_vendored(tmp_path, lock, proofs=None)
    assert check.refused == ("no Factory-held, signed record of the vendored blocks",)
    assert not check.ok


@pytest.mark.parametrize(
    "edit, expected",
    [
        (lambda e: e.update(commit="beef"), "blk_a: the lock's row was edited after CLONER (commit)"),
        (
            lambda e: e.update({so.VENDORED_HASH_KEY: "sha256:forged"}),
            "blk_a: the lock's vendored hash is not the copy the Factory cloned",
        ),
    ],
    ids=["commit", "vendored-hash"],
)
def test_a_lock_row_edited_after_cloner_is_refused(tmp_path, edit, expected):
    _block(tmp_path, "blk_a")
    lock = _lock(tmp_path, "blk_a")
    edit(lock["blocks"]["blk_a"])
    assert _verify(tmp_path, lock).refused == (expected,)


@pytest.mark.parametrize("bid", ["blk_a", "blk_q"])
def test_a_row_removed_from_the_lock_is_refused(tmp_path, bid):
    _block(tmp_path, bid)
    lock = _lock(tmp_path, bid)
    del lock["blocks"][bid]
    assert _verify(tmp_path, lock).refused == (
        f"{bid}: the lock no longer names a block the Factory vendored",
    )


@pytest.mark.parametrize("bid", ["blk_a", "blk_q"])
def test_an_override_row_over_the_unchanged_copy_is_refused(tmp_path, bid):
    _block(tmp_path, bid)
    lock = _lock(tmp_path, bid)
    _prove(tmp_path, bid)
    lock[so.OVERRIDES_KEY] = {bid: _record(tmp_path, lock, bid).to_dict()}
    assert _verify(tmp_path, lock).refused == (
        f"{bid}: an override is recorded, but the block is the vendored copy",
    )


@pytest.mark.parametrize("value", ["s3cret-one", "  s3cret-two  "])
def test_the_proof_key_is_taken_once_and_leaves_the_environment(monkeypatch, value):
    monkeypatch.setattr(so, "_process_key", so._KEY_UNREAD)
    env = {so.PROOF_KEY_ENV: value, "OTHER": "kept"}
    assert so.take_proof_key(env) == value.strip().encode("utf-8")
    assert env == {"OTHER": "kept"}
    env[so.PROOF_KEY_ENV] = "a-later-value"
    assert so.take_proof_key(env) == value.strip().encode("utf-8")


@pytest.mark.parametrize("env", [{}, {so.PROOF_KEY_ENV: "   "}])
def test_no_proof_key_is_none(monkeypatch, env):
    monkeypatch.setattr(so, "_process_key", so._KEY_UNREAD)
    assert so.take_proof_key(dict(env)) is None


def test_a_coding_agent_child_never_gets_the_proof_key():
    from app.factory.build.agent_confine import scoped_child_env

    parent = {so.PROOF_KEY_ENV: "s3cret", "PATH": "/usr/bin"}
    child = scoped_child_env(parent, {so.PROOF_KEY_ENV: "s3cret"}, provider_key=so.PROOF_KEY_ENV)
    assert so.PROOF_KEY_ENV not in child
    assert "s3cret" not in child.values()


@pytest.mark.parametrize(
    "where, refused",
    [
        ("vol/build", True),
        ("vol/build/docs/proofs", True),
        ("vol/build-proofs", True),
        ("vol", True),
        ("factory/proofs", False),
    ],
    ids=["the-product", "inside-product", "sibling-build", "the-volume", "factory-data"],
)
def test_proofs_are_placed_only_off_the_build_volume(tmp_path, where, refused):
    workspace = tmp_path / "vol" / "build"
    workspace.mkdir(parents=True)
    why = so.placement_refusal(tmp_path / where, workspace)
    assert (why is not None) is refused


@pytest.mark.parametrize("rw", ["agent-rw", "agent-rw/deeper"])
def test_proofs_are_never_placed_under_or_above_a_root_the_agent_writes(tmp_path, rw):
    workspace = tmp_path / "vol" / "build"
    workspace.mkdir(parents=True)
    assert so.placement_refusal(tmp_path / "agent-rw", workspace, [tmp_path / rw]) == (
        "exam proofs would sit where the coding agent may write"
    )


@pytest.mark.parametrize("build_id", ["build:aaa", "build:bbb"])
def test_a_proof_store_opens_per_build_under_the_factory_base(tmp_path, build_id):
    workspace = tmp_path / "vol" / "build"
    workspace.mkdir(parents=True)
    store = so.open_proof_store(build_id, workspace, key=_KEY, base=tmp_path / "factory")
    assert store.build_id == build_id
    assert so.is_inside(store.root, tmp_path / "factory")
    assert _KEY not in repr(store).encode("utf-8")
    other = so.open_proof_store(build_id + "x", workspace, key=_KEY, base=tmp_path / "factory")
    assert other.root != store.root


@pytest.mark.parametrize(
    "build_id, key, base, why",
    [
        ("build:a", None, "factory", "is not set"),
        ("build:a", b"", "factory", "is not set"),
        ("  ", _KEY, "factory", "needs the build id"),
        ("build:a", _KEY, "vol", "on the build volume"),
    ],
    ids=["no-key", "empty-key", "no-build", "on-the-volume"],
)
def test_a_proof_store_is_refused_without_key_build_or_safe_place(tmp_path, build_id, key, base, why):
    workspace = tmp_path / "vol" / "build"
    workspace.mkdir(parents=True)
    with pytest.raises(so.OverrideError, match=why):
        so.open_proof_store(build_id, workspace, key=key, base=tmp_path / base)


@pytest.mark.parametrize("env, expected", [({so.PROOFS_DIR_ENV: "/data/p"}, "/data/p"), ({}, None)])
def test_the_proofs_base_is_the_factory_setting_or_the_factory_home(env, expected):
    base = so.proofs_base(env)
    if expected:
        assert base == so.Path(expected)
    else:
        assert base == so.Path.home() / ".cerebrum-factory" / "exam_proofs"
