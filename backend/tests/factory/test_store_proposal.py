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
from app.factory.build.store_override import (
    OVERRIDES_KEY,
    VENDORED_HASH_KEY,
    ExamProof,
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
    _prove(tmp_path, case)
    return lock, vendored, staging


def _proofs(root: Path) -> Path:
    """The Factory-held proofs directory: a sibling of the product, outside it."""
    return root.parent / (root.name + "-proofs")


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
    write_proof(_proofs(root), ExamProof(**fields))


def _passed(case: dict) -> RepairResult:
    return RepairResult(case["block"], True, case["exam"], case["score"])


def _accept(root: Path, lock: dict, result: RepairResult, **kw):
    return sp.accept_repair(root, lock, result, proofs_dir=_proofs(root), **kw)


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
    check = verify_vendored(tmp_path, lock, proofs_dir=_proofs(tmp_path))
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
    lock["blocks"][case["block"]][VENDORED_HASH_KEY] = ""
    with pytest.raises(sp.ProposalError, match="no recorded hash of the vendored copy"):
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
    for path in _proofs(root).iterdir():
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
            tmp_path, lock, _passed(case), build_id=case["build"], proofs_dir=tmp_path / inside
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


# -- F-3: the installer puts the examined repair in place, or changes nothing --


def _install(root: Path, lock: dict, case: dict, **kw):
    return sp.install_repair(
        root, lock, _passed(case), build_id=case["build"], proofs_dir=_proofs(root), **kw
    )


def _proposals_text(root: Path) -> str:
    path = sp.proposals_path(root)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_an_installed_repair_stands_in_the_vendored_place_and_passes_the_lock_check(tmp_path, case):
    lock, vendored, staging = _product(tmp_path, case)
    original = tree_hash(vendored)

    assert _install(tmp_path, lock, case) is None

    assert tree_hash(vendored) == tree_hash(staging) != original
    record = lock[OVERRIDES_KEY][case["block"]]
    assert record["replaces_hash"] == original
    assert record["override_hash"] == tree_hash(staging)
    check = verify_vendored(tmp_path, lock, proofs_dir=_proofs(tmp_path))
    assert check.ok and check.overridden == (case["block"],)
    assert [r["block_id"] for r in sp.filed_proposals(tmp_path)] == [case["block"]]
    assert not vendored.with_name(vendored.name + sp.INSTALL_BACKUP_SUFFIX).exists()


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["block"])
def test_a_repair_without_its_proof_is_not_installed_and_nothing_changes(tmp_path, case):
    lock, vendored, _ = _product(tmp_path, case)
    _prove(tmp_path, case, passed=False)
    before = (tree_hash(vendored), json.dumps(lock, sort_keys=True))

    why = _install(tmp_path, lock, case)

    assert why and case["block"] in why
    assert (tree_hash(vendored), json.dumps(lock, sort_keys=True)) == before
    assert sp.filed_proposals(tmp_path) == []


def _tamper_other_block(root: Path, lock: dict) -> None:
    """A second vendored block whose bytes differ from its recorded copy, so
    the lock check refuses the product whatever this repair does."""
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
def test_an_install_the_lock_check_refuses_is_rolled_back(tmp_path, case):
    lock, vendored, _ = _product(tmp_path, case)
    _tamper_other_block(tmp_path, lock)
    before = (tree_hash(vendored), json.dumps(lock, sort_keys=True))

    why = _install(tmp_path, lock, case)

    assert why.startswith("installed repair failed the lock check: ")
    assert "frobnic" in why
    assert (tree_hash(vendored), json.dumps(lock, sort_keys=True)) == before
    assert OVERRIDES_KEY not in lock
    assert sp.filed_proposals(tmp_path) == []
    assert not vendored.with_name(vendored.name + sp.INSTALL_BACKUP_SUFFIX).exists()


def test_a_rolled_back_install_keeps_a_proposal_filed_before_it(tmp_path):
    case = _CASES[0]
    lock, _, _ = _product(tmp_path, case)
    _accept(tmp_path, dict(lock), _passed(case), build_id=case["build"])
    filed = _proposals_text(tmp_path)
    assert filed
    _tamper_other_block(tmp_path, lock)

    assert _install(tmp_path, lock, case) is not None
    assert _proposals_text(tmp_path) == filed


def test_a_stale_backup_from_an_earlier_install_is_replaced(tmp_path):
    case = _CASES[1]
    lock, vendored, staging = _product(tmp_path, case)
    stale = vendored.with_name(vendored.name + sp.INSTALL_BACKUP_SUFFIX)
    _write(stale, {"left.py": "OLD = 1\n"})

    assert _install(tmp_path, lock, case) is None
    assert tree_hash(vendored) == tree_hash(staging)
    assert not stale.exists()


def test_withdrawing_a_proposal_removes_only_that_row(tmp_path):
    root = tmp_path / "shared"
    rows = [{"proposal_id": "p-one", "n": 1}, {"proposal_id": "p-two", "n": 2}]
    path = sp.proposals_path(root)
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    assert sp.withdraw_proposal(root, "p-absent") is False
    assert sp.filed_proposals(root) == rows
    assert sp.withdraw_proposal(root, "p-one") is True
    assert sp.filed_proposals(root) == [rows[1]]
