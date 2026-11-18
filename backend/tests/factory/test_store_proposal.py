"""R6 (Advisor 12:13Z; order 18:36Z item A2): every accepted repair of a Store
block is filed, by itself, as an upgrade proposal the Store side can pick up.

Exam first: an accepted repair records its override AND files one proposal row
(block, Store version replaced, patch on Store paths, exam id and score,
defects, build); a repair that did not pass is neither recorded nor filed;
filing is idempotent; the patch is one the Store's Carry-Back intake reads as
block-level. Every block id, file name and literal here is invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.factory.build import store_proposal as sp
from app.factory.build import store_override as so
from app.factory.build.store_override import (
    OVERRIDES_KEY,
    VENDORED_HASH_KEY,
    ExamProof,
    ProofStore,
    StoreDefect,
    tree_hash,
    verify_vendored,
    write_proof,
)
from app.factory.build.store_repair import RepairResult

#: The Store's Carry-Back block-path rule (Cerebrum-Blocks carry_back/classify.py).
_CARRY_BACK_BLOCK = re.compile(r"(?:^|/)block_registry/([^/]+)/")

_CASES = [
    {
        "block": "zorblat",
        "files": {"block.py": "def run(x):\n    return x\n", "block.json": '{"id": "zorblat"}\n'},
        "repair": {
            "block.py": "def run(x):\n    if x is None:\n        raise ValueError('x')\n    return x\n"
        },
        "source": "store:origin-one",
        "commit": "c0ffee1",
        "build": "build-alpha",
        "exam": "exam:zorblat:aaaa",
        "score": 1.0,
    },
    {
        "block": "quxbar",
        "files": {"block.py": "VALUE = 1\n", "lib/util.py": "def f():\n    return 2\n"},
        "repair": {"lib/util.py": "def f():\n    return 3\n", "lib/extra.py": "X = 4\n"},
        "source": "store:origin-two",
        "commit": "beef002",
        "build": "build-beta",
        "exam": "exam:quxbar:bbbb",
        "score": 0.875,
    },
]


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _product(tmp_path: Path, case: dict):
    """A product with one vendored block, its lock, and a staged repair."""
    bid = case["block"]
    vendored = tmp_path / "vendor" / "blocks" / bid
    _write(vendored, case["files"])
    lock = {
        "blocks": {
            bid: {
                "source": case["source"],
                "commit": case["commit"],
                "path": f"vendor/blocks/{bid}",
                VENDORED_HASH_KEY: tree_hash(vendored),
            }
        }
    }
    staging = tmp_path / "repair" / bid
    _write(staging, case["files"])
    _write(staging, case["repair"])
    so.record_vendored(
        _proofs(tmp_path, case["build"]),
        {bid: tree_hash(vendored)},
        rows={bid: {"source": case["source"], "commit": case["commit"], "path": f"vendor/blocks/{bid}"}},
    )
    _prove(tmp_path, case)
    return lock, vendored, staging


#: The Factory's signing key in these tests (the real one is never in a test).
_KEY = b"factory-test-key"


def _proofs(root: Path, build_id: str, key: bytes = _KEY) -> ProofStore:
    """The build's Factory-held proof store (placement is tested in
    test_store_override; here it only has to be outside the product)."""
    return ProofStore(root=root.parent / (root.name + "-proofs"), build_id=build_id, key=key)


def _prove(root: Path, case: dict, **over) -> None:
    """The Factory's proof that the staged repair passed the case's exam."""
    fields = dict(
        exam_id=case["exam"],
        block_id=case["block"],
        examined_hash=tree_hash(root / "repair" / case["block"]),
        passed=True,
        score=case["score"],
    )
    fields.update(over)
    write_proof(_proofs(root, case["build"]), ExamProof(**fields))


def _passed(case: dict) -> RepairResult:
    return RepairResult(case["block"], True, case["exam"], case["score"])


def _accept(root: Path, lock: dict, result: RepairResult, *, build_id: str, **kw):
    return sp.accept_repair(root, lock, result, proofs=_proofs(root, build_id), **kw)


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_an_accepted_repair_records_its_override_and_files_one_proposal(tmp_path, case):
    lock, vendored, staging = _product(tmp_path, case)
    defect = StoreDefect(
        case["block"], f"vendor/blocks/{case['block']}/block.py", "crash", {"x": None}
    )
    proposal = _accept(tmp_path, lock, _passed(case), build_id=case["build"], defects=[defect])

    record = lock[OVERRIDES_KEY][case["block"]]
    assert record["exam_id"] == case["exam"] and record["exam_passed"] is True
    assert record["replaces_hash"] == tree_hash(vendored)
    assert record["override_hash"] == tree_hash(staging)
    assert record["store_commit"] == case["commit"]
    assert record["reason"] == sp.REPAIR_REASON

    rows = sp.filed_proposals(tmp_path)
    assert rows == [proposal.row()]
    row = rows[0]
    assert row["block_id"] == case["block"]
    assert row["store_source"] == case["source"]
    assert row["store_commit"] == case["commit"]
    assert row["replaces_hash"] == tree_hash(vendored)
    assert row["override_hash"] == tree_hash(staging)
    assert row["exam_id"] == case["exam"]
    assert row["exam_score"] == case["score"]
    assert row["build_id"] == case["build"]
    assert row["defects"] == [{"path": defect.path, "kind": "crash", "failing_input": {"x": None}}]
    assert row["proposal_id"] == sp.proposal_id_of(
        case["block"], tree_hash(vendored), tree_hash(staging)
    )
    assert row["patch"] == sp.store_patch(vendored, staging, case["block"])
    assert row["patch"]
    assert sp.proposals_path(tmp_path) == tmp_path / "docs" / "store_upgrade_proposals.jsonl"


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_the_recorded_override_passes_the_lock_check_once_the_repair_is_installed(tmp_path, case):
    lock, vendored, staging = _product(tmp_path, case)
    _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    _write(vendored, case["repair"])
    for rel in set(sp._files(vendored)) - set(sp._files(staging)):
        (vendored / rel).unlink()
    check = verify_vendored(tmp_path, lock, proofs=_proofs(tmp_path, case["build"]))
    assert check.ok and check.overridden == (case["block"],)


@pytest.mark.parametrize(
    "result",
    [
        RepairResult("zorblat", False, "exam:zorblat:aaaa", 0.5, "still fails: crash"),
        RepairResult("zorblat", True, "", 1.0),
        RepairResult("zorblat", None, "exam:zorblat:aaaa", 1.0),  # type: ignore[arg-type]
    ],
    ids=["failed", "no-exam", "not-true"],
)
def test_a_repair_that_did_not_pass_is_neither_recorded_nor_filed(tmp_path, result):
    lock, _, _ = _product(tmp_path, _CASES[0])
    with pytest.raises(sp.ProposalError, match="did not pass its exam"):
        _accept(tmp_path, lock, result, build_id="build-alpha")
    assert OVERRIDES_KEY not in lock
    assert sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_block_the_lock_never_vendored_is_refused(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    lock["blocks"] = {"other": "not-a-mapping"} if case is _CASES[0] else {}
    with pytest.raises(sp.ProposalError, match="no such block"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_no_staged_repair_is_refused(tmp_path, case):
    lock, _, staging = _product(tmp_path, case)
    for path in sorted(staging.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    staging.rmdir()
    with pytest.raises(sp.ProposalError, match="no staged repair"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_block_with_no_recorded_vendored_hash_is_refused(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    (_proofs(tmp_path, case["build"]).root / so.VENDORED_RECORD).unlink()
    with pytest.raises(sp.ProposalError, match="no signed record of the vendored copy"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_filing_the_same_repair_twice_is_one_row(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    first = _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert sp.file_proposal(tmp_path, first) is False
    _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert [r["proposal_id"] for r in sp.filed_proposals(tmp_path)] == [first.proposal_id]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)], ids=["as-listed", "reversed"])
def test_two_different_repairs_are_two_rows_in_filing_order(tmp_path, order):
    filed = [_CASES[i] for i in order]
    for case in filed:
        lock, _, _ = _product(tmp_path, case)
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert [r["block_id"] for r in sp.filed_proposals(tmp_path)] == [c["block"] for c in filed]


@pytest.mark.parametrize(
    "args, other",
    [
        (("zorblat", "sha256:1", "sha256:2"), ("zorblat", "sha256:1", "sha256:3")),
        (("quxbar", "sha256:9", "sha256:8"), ("zorblat", "sha256:9", "sha256:8")),
    ],
)
def test_the_proposal_id_is_stable_and_names_block_copy_and_repair(args, other):
    assert sp.proposal_id_of(*args) == sp.proposal_id_of(*args)
    assert sp.proposal_id_of(*args) != sp.proposal_id_of(*other)
    assert sp.proposal_id_of(*args).startswith(f"proposal:{args[0]}:")


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_the_patch_is_on_store_paths_and_the_carry_back_rule_reads_it_as_this_block(tmp_path, case):
    _, vendored, staging = _product(tmp_path, case)
    patch = sp.store_patch(vendored, staging, case["block"])
    headers = [line.split(" ")[-1] for line in patch.splitlines() if line.startswith("diff --git ")]
    assert sorted(headers) == sorted(
        f"b/block_registry/{case['block']}/{rel}" for rel in case["repair"]
    )
    assert {_CARRY_BACK_BLOCK.search(h).group(1) for h in headers} == {case["block"]}
    assert "vendor/blocks" not in patch and "repair/" not in patch
    for rel, text in case["repair"].items():
        for line in text.splitlines():
            assert f"\n+{line}\n" in patch or f"\n {line}\n" in patch
    assert any(row.startswith("+") and not row.startswith("+++") for row in patch.splitlines())


@pytest.mark.parametrize(
    "old, new, want",
    [
        ({"a.py": "x = 1\n"}, {}, ["--- a/block_registry/blk/a.py", "+++ /dev/null", "-x = 1"]),
        ({}, {"b.py": "y = 2\n"}, ["--- /dev/null", "+++ b/block_registry/blk/b.py", "+y = 2"]),
    ],
    ids=["removed", "added"],
)
def test_a_removed_or_added_file_is_in_the_patch(tmp_path, old, new, want):
    _write(tmp_path / "old", old)
    _write(tmp_path / "new", new)
    (tmp_path / "old").mkdir(exist_ok=True)
    (tmp_path / "new").mkdir(exist_ok=True)
    lines = sp.store_patch(tmp_path / "old", tmp_path / "new", "blk").splitlines()
    for line in want:
        assert line in lines


@pytest.mark.parametrize("text", ["k = 1\r\nm = 2\r\n", "p = 3\r\n"])
def test_line_endings_alone_are_no_change(tmp_path, text):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    (tmp_path / "old" / "f.py").write_bytes(text.encode("utf-8"))
    (tmp_path / "new" / "f.py").write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    assert sp.store_patch(tmp_path / "old", tmp_path / "new", "blk") == ""


@pytest.mark.parametrize("old, new", [("a = 1\n", "a = 2"), ("b = 1", "b = 3\n")])
def test_a_missing_final_newline_is_marked_and_lines_stay_separate(tmp_path, old, new):
    _write(tmp_path / "old", {"f.py": old})
    _write(tmp_path / "new", {"f.py": new})
    patch = sp.store_patch(tmp_path / "old", tmp_path / "new", "blk")
    assert "\\ No newline at end of file\n" in patch
    assert patch.endswith("\n")
    for line in patch.splitlines():
        assert not (line.startswith(("-", "+")) and "\\ No newline" in line)


@pytest.mark.parametrize("blob", [b"\xff\xfe\x00one", b"\x80two\x81"])
def test_a_changed_binary_file_is_named_and_an_unchanged_one_is_not(tmp_path, blob):
    for side in ("old", "new"):
        (tmp_path / side).mkdir()
        (tmp_path / side / "same.bin").write_bytes(blob)
    (tmp_path / "old" / "chg.bin").write_bytes(blob)
    (tmp_path / "new" / "chg.bin").write_bytes(blob + b"\xff")
    patch = sp.store_patch(tmp_path / "old", tmp_path / "new", "blk")
    assert patch == (
        "diff --git a/block_registry/blk/chg.bin b/block_registry/blk/chg.bin\n"
        "Binary files a/block_registry/blk/chg.bin and b/block_registry/blk/chg.bin differ\n"
    )


@pytest.mark.parametrize("blob", [b"\xff\x00", b"\x81"])
def test_a_binary_file_added_or_removed_is_named(tmp_path, blob):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "added.bin").write_bytes(blob)
    (tmp_path / "old" / "gone.bin").write_bytes(blob)
    patch = sp.store_patch(tmp_path / "old", tmp_path / "new", "blk")
    assert (
        "Binary files a/block_registry/blk/added.bin and b/block_registry/blk/added.bin differ"
        in patch
    )
    assert (
        "Binary files a/block_registry/blk/gone.bin and b/block_registry/blk/gone.bin differ"
        in patch
    )


@pytest.mark.parametrize("name", ["__pycache__/m.cpython-311.pyc", "sub/__pycache__/n.pyc"])
def test_bytecode_caches_are_not_part_of_the_patch(tmp_path, name):
    _write(tmp_path / "old", {"keep.py": "z = 1\n"})
    _write(tmp_path / "new", {"keep.py": "z = 1\n", name: "cache"})
    assert sp.store_patch(tmp_path / "old", tmp_path / "new", "blk") == ""


@pytest.mark.parametrize("missing", ["old", "new"])
def test_a_missing_tree_is_an_empty_side(tmp_path, missing):
    present = "new" if missing == "old" else "old"
    _write(tmp_path / present, {"only.py": "q = 1\n"})
    patch = sp.store_patch(tmp_path / "old", tmp_path / "new", "blk")
    assert "block_registry/blk/only.py" in patch and "q = 1" in patch


@pytest.mark.parametrize(
    "rows", [[{"proposal_id": "p1"}], [{"proposal_id": "p1"}, {"proposal_id": "p2"}]]
)
def test_filed_proposals_reads_every_row_and_skips_blank_lines(tmp_path, rows):
    assert sp.filed_proposals(tmp_path) == []
    path = sp.proposals_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n  \n", encoding="utf-8")
    assert sp.filed_proposals(tmp_path) == rows


# --- F-2: acceptance trusts the Factory-held proof, not the caller's result ---


def _drop_proofs(root: Path, case: dict) -> None:
    for path in _proofs(root, case["build"]).root.glob("*" + so.PROOF_SUFFIX):
        path.unlink()


def _failed_proof(root: Path, case: dict) -> None:
    _prove(root, case, passed=False)


def _other_block_proof(root: Path, case: dict) -> None:
    _prove(root, case, block_id="someother")


def _edited_after_exam(root: Path, case: dict) -> None:
    (root / "repair" / case["block"] / "planted.py").write_text("Y = 9\n", encoding="utf-8")


@pytest.mark.parametrize(
    "spoil, why",
    [
        (_drop_proofs, "no Factory-held proof of its exam"),
        (_failed_proof, "does not show it passing its exam"),
        (_other_block_proof, "does not show it passing its exam"),
        (_edited_after_exam, "not the tree its exam examined"),
    ],
    ids=["no-proof", "failed", "other-block", "edited-after-exam"],
)
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_passed_result_without_a_matching_factory_proof_is_neither_recorded_nor_filed(
    tmp_path, case, spoil, why
):
    lock, _, _ = _product(tmp_path, case)
    spoil(tmp_path, case)
    with pytest.raises(sp.ProposalError, match=why):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("inside", [".", "docs/proofs"])
def test_proofs_held_inside_the_product_are_refused(tmp_path, inside):
    case = _CASES[0]
    lock, _, _ = _product(tmp_path, case)
    with pytest.raises(sp.ProposalError, match="inside the product tree"):
        sp.accept_repair(
            tmp_path,
            lock,
            _passed(case),
            proofs=ProofStore(root=tmp_path / inside, build_id=case["build"], key=_KEY),
        )
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("path", ["vendor/blocks/other", "lib/zorblat", None])
def test_a_block_vendored_anywhere_but_its_own_path_is_refused(tmp_path, path):
    case = _CASES[0]
    lock, _, _ = _product(tmp_path, case)
    lock["blocks"][case["block"]]["path"] = path
    with pytest.raises(sp.ProposalError, match="not vendored at vendor/blocks/zorblat"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_the_filed_score_is_the_proofs_not_the_callers(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    claimed = RepairResult(case["block"], True, case["exam"], 0.01)
    proposal = _accept(tmp_path, lock, claimed, build_id=case["build"])
    assert proposal.exam_score == case["score"]
    assert sp.filed_proposals(tmp_path)[0]["exam_score"] == case["score"]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_failed_filing_leaves_the_lock_untouched(tmp_path, case, monkeypatch):
    lock, _, _ = _product(tmp_path, case)

    def fail(workspace, proposal):
        raise OSError("disk full")

    monkeypatch.setattr(sp, "file_proposal", fail)
    with pytest.raises(OSError, match="disk full"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock


@pytest.mark.parametrize(
    "corrupt", ['{"proposal_id": "torn', "[1, 2]"], ids=["torn-line", "not-an-object"]
)
def test_a_corrupt_proposals_line_is_skipped_kept_and_filing_goes_on(tmp_path, corrupt):
    case = _CASES[0]
    lock, _, _ = _product(tmp_path, case)
    path = sp.proposals_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"proposal_id": "p0"}) + "\n" + corrupt, encoding="utf-8")
    proposal = _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert [r["proposal_id"] for r in sp.filed_proposals(tmp_path)] == ["p0", proposal.proposal_id]
    assert path.read_text(encoding="utf-8").splitlines()[1] == corrupt
    assert lock[OVERRIDES_KEY][case["block"]]["exam_id"] == case["exam"]
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


# --- F-2 (grader 19:35Z): signed, build-bound proofs; accept is atomic ---------


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_proof_signed_with_another_key_is_neither_recorded_nor_filed(tmp_path, case):
    lock, _, staging = _product(tmp_path, case)
    forged = _proofs(tmp_path, case["build"], key=b"a-key-the-writer-made-up")
    write_proof(forged, ExamProof(case["exam"], case["block"], tree_hash(staging), True, 1.0))
    with pytest.raises(sp.ProposalError, match="no Factory-held proof of its exam"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_proof_from_another_build_is_neither_recorded_nor_filed(tmp_path, case):
    lock, _, staging = _product(tmp_path, case)
    other = _proofs(tmp_path, case["build"] + "-other")
    write_proof(other, ExamProof(case["exam"], case["block"], tree_hash(staging), True, 1.0))
    with pytest.raises(sp.ProposalError, match="no Factory-held proof of its exam"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_the_proposal_names_the_build_of_the_proof_store(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    proposal = _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert proposal.build_id == case["build"]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_no_proof_store_is_neither_recorded_nor_filed(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    with pytest.raises(sp.ProposalError, match="no proof store"):
        sp.accept_repair(tmp_path, lock, _passed(case), proofs=None)
    assert OVERRIDES_KEY not in lock and sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_failure_recording_the_override_after_filing_withdraws_the_proposal(
    tmp_path, case, monkeypatch
):
    lock, _, _ = _product(tmp_path, case)
    path = sp.proposals_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"proposal_id": "earlier"}) + "\n", encoding="utf-8")
    seen = []

    def fail_after_filing(*args, **kwargs):
        seen.append([r["proposal_id"] for r in sp.filed_proposals(tmp_path)])
        raise OSError("lock write failed")

    monkeypatch.setattr(sp, "record_override", fail_after_filing)
    with pytest.raises(OSError, match="lock write failed"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert len(seen) == 1 and len(seen[0]) == 2, "the proposal was filed before the override"
    assert [r["proposal_id"] for r in sp.filed_proposals(tmp_path)] == ["earlier"]
    assert OVERRIDES_KEY not in lock


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_failure_after_filing_keeps_a_row_this_call_did_not_file(tmp_path, case, monkeypatch):
    lock, _, _ = _product(tmp_path, case)
    first = _accept(tmp_path, lock, _passed(case), build_id=case["build"])

    def fail(*args, **kwargs):
        raise OSError("lock write failed")

    monkeypatch.setattr(sp, "record_override", fail)
    with pytest.raises(OSError):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert [r["proposal_id"] for r in sp.filed_proposals(tmp_path)] == [first.proposal_id]


@pytest.mark.parametrize("ids", [["p1", "p2", "p3"], ["only"]])
def test_withdrawing_a_proposal_removes_that_row_only(tmp_path, ids):
    path = sp.proposals_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps({"proposal_id": i}) + "\n" for i in ids), encoding="utf-8")
    assert sp.withdraw_proposal(tmp_path, ids[0]) is True
    assert [r["proposal_id"] for r in sp.filed_proposals(tmp_path)] == ids[1:]
    assert sp.withdraw_proposal(tmp_path, ids[0]) is False
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


# -- F-2 (grader 20:30Z): the accepted lock is saved; a retry is idempotent --


def _saved(root: Path) -> dict:
    return json.loads((root / sp.LOCK_FILE).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_an_accepted_override_is_saved_to_the_products_lock(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    saved = _saved(tmp_path)
    assert saved == json.loads(json.dumps(lock))
    assert saved[OVERRIDES_KEY][case["block"]]["exam_id"] == case["exam"]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_failed_save_restores_the_lock_and_withdraws_the_row(tmp_path, case, monkeypatch):
    lock, _, _ = _product(tmp_path, case)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sp, "save_lock", fail)
    with pytest.raises(OSError, match="disk full"):
        _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert OVERRIDES_KEY not in lock
    assert sp.filed_proposals(tmp_path) == []
    assert not (tmp_path / sp.LOCK_FILE).exists()


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_retry_after_a_crash_between_filing_and_saving_completes_once(tmp_path, case, monkeypatch):
    lock, _, _ = _product(tmp_path, case)
    with monkeypatch.context() as m:
        # The crash: the row is filed, then the process dies before the lock
        # is saved, so no compensation runs.
        def crash(*args, **kwargs):
            raise OSError("process died")

        m.setattr(sp, "save_lock", crash)
        m.setattr(sp, "withdraw_proposal", lambda *a, **k: False)
        with pytest.raises(OSError):
            _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert len(sp.filed_proposals(tmp_path)) == 1
    assert not (tmp_path / sp.LOCK_FILE).exists()

    proposal = _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert [r["proposal_id"] for r in sp.filed_proposals(tmp_path)] == [proposal.proposal_id]
    assert _saved(tmp_path)[OVERRIDES_KEY][case["block"]]["override_hash"] == proposal.override_hash

    once = (tmp_path / sp.LOCK_FILE).read_bytes()
    rows = sp.filed_proposals(tmp_path)
    again = _accept(tmp_path, lock, _passed(case), build_id=case["build"])
    assert again == proposal
    assert sp.filed_proposals(tmp_path) == rows
    assert (tmp_path / sp.LOCK_FILE).read_bytes() == once


# -- F-3: the installer puts the examined repair in place, or changes nothing --


def _install(root: Path, lock: dict, case: dict, **kw):
    return sp.install_repair(
        root, lock, _passed(case), proofs=_proofs(root, case["build"]), **kw
    )


def _proposals_text(root: Path) -> str:
    path = sp.proposals_path(root)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _saved_lock(root: Path):
    path = root / sp.LOCK_FILE
    return path.read_bytes() if path.is_file() else None


def _backup_of(vendored: Path) -> Path:
    return vendored.with_name(vendored.name + sp.INSTALL_BACKUP_SUFFIX)


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_an_installed_repair_stands_in_place_and_is_saved_to_the_lock_on_disk(tmp_path, case):
    lock, vendored, staging = _product(tmp_path, case)
    original = tree_hash(vendored)

    assert _install(tmp_path, lock, case) is None

    assert tree_hash(vendored) == tree_hash(staging) != original
    record = lock[OVERRIDES_KEY][case["block"]]
    assert record["replaces_hash"] == original
    assert record["override_hash"] == tree_hash(staging)
    on_disk = json.loads((tmp_path / sp.LOCK_FILE).read_text(encoding="utf-8"))
    assert on_disk[OVERRIDES_KEY][case["block"]] == record
    check = verify_vendored(tmp_path, on_disk, proofs=_proofs(tmp_path, case["build"]))
    assert check.ok and check.overridden == (case["block"],)
    assert [r["block_id"] for r in sp.filed_proposals(tmp_path)] == [case["block"]]
    assert not _backup_of(vendored).exists()


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_repair_without_its_proof_is_not_installed_and_nothing_changes(tmp_path, case):
    lock, vendored, _ = _product(tmp_path, case)
    _prove(tmp_path, case, passed=False)
    before = (tree_hash(vendored), json.dumps(lock, sort_keys=True), _saved_lock(tmp_path))

    why = _install(tmp_path, lock, case)

    assert why and case["block"] in why
    assert (tree_hash(vendored), json.dumps(lock, sort_keys=True), _saved_lock(tmp_path)) == before
    assert sp.filed_proposals(tmp_path) == []


@pytest.mark.parametrize(
    "prior",
    [{}, {"elsewhere": {"override_hash": "1" * 64, "exam_id": "exam:elsewhere:cccc"}}],
    ids=["empty", "another-block"],
)
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_refused_install_keeps_the_overrides_the_lock_already_had(tmp_path, case, prior):
    """A lock that already carries an overrides table (empty, or recording
    another block) keeps that table, as it was, when this install is refused;
    the table is not dropped just because this block was not added."""
    lock, vendored, _ = _product(tmp_path, case)
    lock[OVERRIDES_KEY] = json.loads(json.dumps(prior))
    sp.save_lock(tmp_path, lock)
    _prove(tmp_path, case, passed=False)
    saved = _saved_lock(tmp_path)

    why = _install(tmp_path, lock, case)

    assert why and case["block"] in why
    assert lock[OVERRIDES_KEY] == prior
    assert _saved_lock(tmp_path) == saved
    assert json.loads(saved.decode("utf-8"))[OVERRIDES_KEY] == prior


def _tamper_other_block(root: Path, lock: dict) -> None:
    """A second block whose bytes differ from its lock row and that the
    Factory never recorded, so the lock check refuses the product whatever
    this repair does."""
    other = root / "vendor" / "blocks" / "frobnic"
    _write(other, {"core.py": "A = 1\n"})
    lock["blocks"]["frobnic"] = {
        "source": "store:origin-three",
        "commit": "abc1234",
        "path": "vendor/blocks/frobnic",
        VENDORED_HASH_KEY: tree_hash(other),
    }
    _write(other, {"core.py": "A = 2\n"})


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_an_install_the_lock_check_refuses_is_rolled_back_on_disk_too(tmp_path, case):
    lock, vendored, _ = _product(tmp_path, case)
    _tamper_other_block(tmp_path, lock)
    sp.save_lock(tmp_path, lock)
    before = (tree_hash(vendored), json.dumps(lock, sort_keys=True), _saved_lock(tmp_path))

    why = _install(tmp_path, lock, case)

    assert why.startswith("installed repair failed the lock check: ")
    assert "frobnic" in why
    assert (tree_hash(vendored), json.dumps(lock, sort_keys=True), _saved_lock(tmp_path)) == before
    assert OVERRIDES_KEY not in lock
    assert sp.filed_proposals(tmp_path) == []
    assert not _backup_of(vendored).exists()


@pytest.mark.parametrize(
    "stage, error",
    [("copy", OSError), ("verify", RuntimeError), ("save", PermissionError)],
)
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_any_failure_after_the_backup_move_is_undone_and_named(
    tmp_path, monkeypatch, case, stage, error
):
    """The copy raising halfway, the lock check raising, or the final save
    raising: the partial copy is removed, the vendored copy restored, the
    saved lock and the proposals file are as they were, and the reason is
    returned, not raised."""
    lock, vendored, _ = _product(tmp_path, case)
    sp.save_lock(tmp_path, lock)
    before = (
        tree_hash(vendored),
        json.dumps(lock, sort_keys=True),
        _saved_lock(tmp_path),
        _proposals_text(tmp_path),
    )
    real_copytree, real_save = sp.shutil.copytree, sp.save_lock

    def half_copy(src, dst, **kw):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "half.py").write_text("PARTIAL = 1\n", encoding="utf-8")
        raise error("copy stopped halfway")

    def verify_raises(*_a, **_k):
        raise error("check crashed")

    saves = []

    def save_raises_on_install(root, data):
        saves.append(1)
        if len(saves) > 1:  # the first save is accept_repair's on the trial lock
            raise error("disk full")
        return real_save(root, data)

    if stage == "copy":
        monkeypatch.setattr(sp.shutil, "copytree", half_copy)
    elif stage == "verify":
        monkeypatch.setattr(sp, "verify_vendored", verify_raises)
    else:
        monkeypatch.setattr(sp, "save_lock", save_raises_on_install)

    why = _install(tmp_path, lock, case)
    monkeypatch.setattr(sp.shutil, "copytree", real_copytree)

    assert why == f"install failed and was undone ({error.__name__})"
    assert (
        tree_hash(vendored),
        json.dumps(lock, sort_keys=True),
        _saved_lock(tmp_path),
        _proposals_text(tmp_path),
    ) == before
    assert not (vendored / "half.py").exists()
    assert not _backup_of(vendored).exists()


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_rolled_back_install_keeps_a_proposal_filed_before_it(tmp_path, case):
    lock, _, _ = _product(tmp_path, case)
    _accept(tmp_path, json.loads(json.dumps(lock)), _passed(case), build_id=case["build"])
    filed = _proposals_text(tmp_path)
    assert filed
    _tamper_other_block(tmp_path, lock)

    assert _install(tmp_path, lock, case) is not None
    assert _proposals_text(tmp_path) == filed


@pytest.mark.parametrize(
    "case, stale_files",
    [(_CASES[0], {"left.py": "OLD = 1\n"}), (_CASES[1], {"a/b.py": "OLD = 2\n"})],
    ids=["one-file", "nested"],
)
def test_a_stale_backup_from_an_earlier_install_is_replaced(tmp_path, case, stale_files):
    lock, vendored, staging = _product(tmp_path, case)
    stale = _backup_of(vendored)
    _write(stale, stale_files)

    assert _install(tmp_path, lock, case) is None
    assert tree_hash(vendored) == tree_hash(staging)
    assert not stale.exists()


# -- F-3b: the start-up check settles what a killed install left ---------------


class _Killed(BaseException):
    """The process dying midway: nothing in install_repair catches it."""


@pytest.mark.parametrize("stage", ["moved-aside", "half-copied", "copied"])
@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_killed_install_is_undone_at_start_up(tmp_path, monkeypatch, case, stage):
    lock, vendored, staging = _product(tmp_path, case)
    sp.save_lock(tmp_path, lock)
    original = tree_hash(vendored)
    real_copytree, real_verify = sp.shutil.copytree, sp.verify_vendored

    def killed_copy(src, dst, **kw):
        if stage == "half-copied":
            Path(dst).mkdir(parents=True)
            (Path(dst) / "half.py").write_text("PARTIAL = 1\n", encoding="utf-8")
        raise _Killed()

    def killed_verify(*_a, **_k):
        raise _Killed()

    if stage == "copied":
        monkeypatch.setattr(sp, "verify_vendored", killed_verify)
    else:
        monkeypatch.setattr(sp.shutil, "copytree", killed_copy)
    with pytest.raises(_Killed):
        _install(tmp_path, json.loads(json.dumps(lock)), case)
    monkeypatch.setattr(sp.shutil, "copytree", real_copytree)
    monkeypatch.setattr(sp, "verify_vendored", real_verify)
    assert _backup_of(vendored).is_dir()
    assert case["block"] in _saved(tmp_path)[OVERRIDES_KEY]

    lines = sp.recover_installs(tmp_path)

    assert lines == [f"{case['block']}: a half-done install was undone; the vendored copy is back"]
    assert tree_hash(vendored) == original
    assert not _backup_of(vendored).exists()
    assert _saved(tmp_path) == json.loads(json.dumps(lock))
    check = verify_vendored(tmp_path, _saved(tmp_path), proofs=_proofs(tmp_path, case["build"]))
    assert check.ok and check.unchanged == (case["block"],)
    assert sp.recover_installs(tmp_path) == []
    assert _install(tmp_path, _saved(tmp_path), case) is None
    assert tree_hash(vendored) == tree_hash(staging)


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_finished_install_only_loses_its_leftover_backup(tmp_path, case):
    lock, vendored, staging = _product(tmp_path, case)
    assert _install(tmp_path, lock, case) is None
    saved = _saved_lock(tmp_path)
    _write(_backup_of(vendored), {"torn.py": "HALF = 1\n"})

    lines = sp.recover_installs(tmp_path)

    assert lines == [f"{case['block']}: the install had finished; its leftover backup was removed"]
    assert not _backup_of(vendored).exists()
    assert tree_hash(vendored) == tree_hash(staging)
    assert _saved_lock(tmp_path) == saved


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_backup_that_is_not_the_recorded_copy_is_left_as_found(tmp_path, case):
    lock, vendored, _ = _product(tmp_path, case)
    sp.save_lock(tmp_path, lock)
    _write(_backup_of(vendored), {"other.py": "OTHER = 1\n"})
    before = (tree_hash(vendored), tree_hash(_backup_of(vendored)), _saved_lock(tmp_path))

    lines = sp.recover_installs(tmp_path)

    assert lines == [
        f"{case['block']}: a half-done install was left as found: "
        "its backup is not the copy the lock records"
    ]
    assert (tree_hash(vendored), tree_hash(_backup_of(vendored)), _saved_lock(tmp_path)) == before


@pytest.mark.parametrize("layout", [None, {"vendor/blocks/zorblat/block.py": "A = 1\n"}])
def test_a_product_with_no_backup_has_nothing_to_recover(tmp_path, layout):
    _write(tmp_path, layout or {})
    assert sp.recover_installs(tmp_path) == []
