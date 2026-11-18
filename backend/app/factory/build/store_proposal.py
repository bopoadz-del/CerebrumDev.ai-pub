"""R6 (Advisor 12:13Z; order 18:36Z item A2): every accepted repair of a
Store block is filed, by itself, as an upgrade proposal for the Store.

An accepted override and its proposal are one step: :func:`accept_repair`
records the override in the product lock (R3, through
:func:`store_override.record_override`, which refuses a repair that did not
pass its exam) and appends the proposal row in the same call. No path records
an override without filing it, and no person is needed for the build to go on.

The row goes to ``docs/store_upgrade_proposals.jsonl`` inside the product (the
spec's design table, row R6). It is filed by the Factory and is never a Store
write: the Store takes it in during its own upgrade and certification pass.
The row carries the block, the Store version it replaces, the unified patch,
the exam id and sealed score, the defects that started the repair (so the
Store can regenerate the same exam) and the build it came from.

The patch is written against the block's path in the Store
(``block_registry/<id>/...``), so the Store's own Carry-Back intake
(``python -m carry_back classify --diff``) reads it as a block-level fix.
Every value is read from the lock, the repair and the two trees; nothing names
a block, product or value.

F-2: the caller's :class:`RepairResult` is not trusted. Acceptance reads the
exam's signed proof from the build's Factory-held :class:`ProofStore` (the
repairer writes it, see :func:`store_override.write_proof`). It accepts only
when that proof shows this block passing on exactly the staged bytes, and the
copy replaced is the one the Factory recorded at CLONER. The block must be
vendored at ``vendor/blocks/<id>``. Every check runs before anything is
written. The proposal is filed (atomically) first and only then is the
override written into the lock and the lock saved to the product's
``blocks.lock.json`` (atomically), so a refusal or a failed filing leaves the
lock untouched; if recording or saving the override fails after filing, the
row is withdrawn.

F-2 (grader 20:30Z): this is compensation, not a transaction. A crash between
filing and saving leaves a filed row with no override behind it. The retry is
the repair: the same repair has the same proposal id, so filing it again files
nothing new, and the retry records and saves the override. A retry of an
accept that already completed changes nothing.
A corrupt proposals line is skipped, not fatal.

F-3: :func:`install_repair` is the installer :func:`store_repair.settle`
calls. It accepts the repair (above) against a trial copy of the lock, puts
the staged repair in place of the vendored copy and re-runs the lock check on
the product as it now stands. Only when that check passes, with this block
counted as a recorded override, is the override in the caller's lock and in
the product's saved ``blocks.lock.json``. Otherwise, and on any exception
after it starts, the partial copy is removed, the vendored copy restored, the
saved lock put back byte for byte, a proposal row this call filed withdrawn,
and the reason returned.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from app.factory.build.store_override import (
    OVERRIDES_KEY,
    VENDOR_BLOCKS,
    VENDORED_HASH_KEY,
    OverrideRecord,
    ProofStore,
    StoreDefect,
    is_inside,
    proof_refusal,
    read_proof,
    record_override,
    tree_hash,
    vendored_at,
    vendored_path,
    vendored_record,
    verify_vendored,
)
from app.factory.build.store_repair import RepairResult
from app.factory.build.store_repair_exam import REPAIR_DIR

#: Where the proposals are filed inside the product (spec design table, R6).
PROPOSALS_FILE = ("docs", "store_upgrade_proposals.jsonl")
#: A Store block's directory in the Store (the Carry-Back intake's block path).
STORE_BLOCK_DIR = "block_registry"
#: The product's lock, where an accepted override is saved.
LOCK_FILE = "blocks.lock.json"
#: The reason an accepted repair's override is recorded with.
REPAIR_REASON = "Store block defect repaired under its exam"
#: Suffix of the vendored copy kept aside while a repair is installed (F-3).
INSTALL_BACKUP_SUFFIX = ".vendored"


class ProposalError(ValueError):
    """A proposal that may not be filed (no accepted repair behind it)."""


@dataclass(frozen=True)
class StoreProposal:
    """R6: one upgrade proposal, as the Store side reads it."""

    proposal_id: str
    block_id: str
    store_source: str
    store_commit: str
    replaces_hash: str
    override_hash: str
    exam_id: str
    exam_score: Optional[float]
    build_id: str
    patch: str
    defects: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    def row(self) -> Dict[str, Any]:
        out = asdict(self)
        out["defects"] = [dict(d) for d in self.defects]
        return out


def _files(root: Path) -> Dict[str, Path]:
    """Every file under ``root`` by posix path (none when ``root`` is absent)."""
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def _text_lines(path: Optional[Path]) -> Optional[List[str]]:
    """The file's LF lines, [] when absent, None when it is not text."""
    if path is None:
        return []
    try:
        text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.splitlines(keepends=True)


def store_patch(vendored_dir: Path | str, repaired_dir: Path | str, block_id: str) -> str:
    """A unified patch from the vendored copy to the repair, on Store paths."""
    old_files = _files(Path(vendored_dir))
    new_files = _files(Path(repaired_dir))
    chunks: List[str] = []
    for rel in sorted(set(old_files) | set(new_files)):
        store_rel = f"{STORE_BLOCK_DIR}/{block_id}/{rel}"
        old_path = old_files.get(rel)
        new_path = new_files.get(rel)
        old = _text_lines(old_path)
        new = _text_lines(new_path)
        if old is None or new is None:
            same = (
                old_path is not None
                and new_path is not None
                and old_path.read_bytes() == new_path.read_bytes()
            )
            if not same:
                chunks.append(f"diff --git a/{store_rel} b/{store_rel}\n")
                chunks.append(f"Binary files a/{store_rel} and b/{store_rel} differ\n")
            continue
        if old == new:
            continue
        chunks.append(f"diff --git a/{store_rel} b/{store_rel}\n")
        for line in difflib.unified_diff(
            old,
            new,
            fromfile=f"a/{store_rel}" if old_path is not None else "/dev/null",
            tofile=f"b/{store_rel}" if new_path is not None else "/dev/null",
        ):
            chunks.append(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            )
    return "".join(chunks)


def _defect_rows(defects: Sequence[StoreDefect]) -> Tuple[Dict[str, Any], ...]:
    return tuple(
        {"path": d.path, "kind": d.kind, "failing_input": d.failing_input} for d in defects
    )


def proposal_id_of(block_id: str, replaces_hash: str, override_hash: str) -> str:
    """One id per (block, copy replaced, repair): filing it twice is one row."""
    body = "\0".join((block_id, replaces_hash, override_hash))
    return "proposal:" + block_id + ":" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def proposals_path(workspace: Path | str) -> Path:
    return Path(workspace).joinpath(*PROPOSALS_FILE)


def filed_proposals(workspace: Path | str) -> List[Dict[str, Any]]:
    """Every proposal row filed in this product, in filing order. A line that
    is not a JSON object (a torn or hand-edited line) is skipped."""
    path = proposals_path(workspace)
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def file_proposal(workspace: Path | str, proposal: StoreProposal) -> bool:
    """Append ``proposal`` once. False when the same proposal is already filed."""
    if any(row.get("proposal_id") == proposal.proposal_id for row in filed_proposals(workspace)):
        return False
    path = proposals_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    if before and not before.endswith("\n"):
        before += "\n"
    line = json.dumps(proposal.row(), sort_keys=True, default=str) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(before + line, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return True


def _write_rows(workspace: Path | str, rows: Sequence[Mapping[str, Any]]) -> None:
    path = proposals_path(workspace)
    text = "".join(json.dumps(dict(r), sort_keys=True, default=str) + "\n" for r in rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def withdraw_proposal(workspace: Path | str, proposal_id: str) -> bool:
    """Remove the row ``proposal_id``. False when it is not filed."""
    rows = filed_proposals(workspace)
    kept = [row for row in rows if row.get("proposal_id") != proposal_id]
    if len(kept) == len(rows):
        return False
    _write_rows(workspace, kept)
    return True


def save_lock(workspace: Path | str, lock: Mapping[str, Any]) -> Path:
    """Write ``lock`` to the product's ``blocks.lock.json``, atomically."""
    path = Path(workspace) / LOCK_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(lock), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _saved_override(workspace: Path | str, record: OverrideRecord) -> bool:
    """True when the product's saved lock already holds exactly ``record``."""
    try:
        saved = json.loads((Path(workspace) / LOCK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    overrides = saved.get(OVERRIDES_KEY) if isinstance(saved, Mapping) else None
    return isinstance(overrides, Mapping) and overrides.get(record.block_id) == record.to_dict()


def accept_repair(
    workspace: Path | str,
    lock: MutableMapping[str, Any],
    result: RepairResult,
    *,
    proofs: Optional[ProofStore],
    defects: Sequence[StoreDefect] = (),
) -> StoreProposal:
    """R3 + R6: record the passed repair as the block's override and file its
    upgrade proposal, in one step. A repair that did not pass its exam, as the
    Factory-held, signed proof in ``proofs`` shows for this build, is neither
    recorded nor filed. The proposal names the build of ``proofs``.

    The override is saved to the product's lock on disk. A refusal or a
    failed filing leaves the lock untouched; a failure in recording or saving
    the override after filing restores the lock and withdraws the row, unless
    the saved lock already holds this override (an earlier accept completed).
    Idempotent: retrying after a crash between filing and saving files
    nothing twice and completes the override."""
    if result.passed is not True or not result.exam_id:
        raise ProposalError(f"repair of {result.block_id!r} did not pass its exam: nothing to file")
    if proofs is None:
        raise ProposalError("the Factory holds no proof store: nothing to file")
    root = Path(workspace)
    if is_inside(proofs.root, root):
        raise ProposalError("exam proofs are held inside the product tree: nothing to file")
    block_id = result.block_id
    entry = (lock.get("blocks") or {}).get(block_id)
    if not isinstance(entry, Mapping):
        raise ProposalError(f"repair of {block_id!r}: the lock vendored no such block")
    if not vendored_at(entry.get("path"), block_id):
        raise ProposalError(f"repair of {block_id!r}: not vendored at {vendored_path(block_id)}")
    staging = root / REPAIR_DIR / block_id
    if not staging.is_dir():
        raise ProposalError(f"repair of {block_id!r}: no staged repair at {REPAIR_DIR}/{block_id}")
    replaces = (vendored_record(proofs) or {}).get(block_id, "")
    if not replaces:
        raise ProposalError(
            f"repair of {block_id!r}: the Factory holds no signed record of the vendored copy"
        )
    override_hash = tree_hash(staging)
    proof = read_proof(proofs, result.exam_id)
    why = proof_refusal(proof, block_id, override_hash)
    if why is not None:
        raise ProposalError(f"repair of {why}")
    record = OverrideRecord(
        block_id=block_id,
        replaces_hash=replaces,
        override_hash=override_hash,
        reason=REPAIR_REASON,
        exam_id=result.exam_id,
        exam_passed=True,
        store_commit=str(entry.get("commit") or ""),
    )
    proposal = StoreProposal(
        proposal_id=proposal_id_of(block_id, replaces, override_hash),
        block_id=block_id,
        store_source=str(entry.get("source") or ""),
        store_commit=record.store_commit,
        replaces_hash=replaces,
        override_hash=override_hash,
        exam_id=result.exam_id,
        exam_score=getattr(proof, "score", None),
        build_id=proofs.build_id,
        patch=store_patch(root.joinpath(*VENDOR_BLOCKS, block_id), staging, block_id),
        defects=_defect_rows(defects),
    )
    previous = lock.get(OVERRIDES_KEY)
    before = dict(previous) if isinstance(previous, Mapping) else None
    file_proposal(root, proposal)
    try:
        record_override(lock, record, vendored_hash=replaces)
        save_lock(root, lock)
    except BaseException:
        if before is None:
            lock.pop(OVERRIDES_KEY, None)
        else:
            lock[OVERRIDES_KEY] = before
        if not _saved_override(root, record):
            withdraw_proposal(root, proposal.proposal_id)
        raise
    return proposal


def _read_bytes(path: Path) -> Optional[bytes]:
    """The file's bytes, or None when there is no such file."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _put_back_lock(root: Path, saved: Optional[bytes]) -> None:
    """The product's saved lock as it was before the install began."""
    path = root / LOCK_FILE
    if saved is None:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(saved)
    os.replace(tmp, path)


def _undo_install(
    root: Path,
    vendored: Path,
    backup: Path,
    *,
    moved: bool,
    saved_lock: Optional[bytes],
    proposal_id: Optional[str],
    filed_before: set,
) -> None:
    """F-3: the product as it was before :func:`install_repair` began: the
    partial copy removed and the vendored copy restored (when it was moved
    aside), the saved lock put back, and a proposal row this call filed
    withdrawn."""
    if moved:
        if vendored.exists():
            shutil.rmtree(vendored)
        os.replace(backup, vendored)
    _put_back_lock(root, saved_lock)
    if proposal_id and proposal_id not in filed_before:
        withdraw_proposal(root, proposal_id)


def install_repair(
    workspace: Path | str,
    lock: MutableMapping[str, Any],
    result: RepairResult,
    *,
    proofs: Optional[ProofStore],
    defects: Sequence[StoreDefect] = (),
) -> Optional[str]:
    """F-3: install the passed repair of ``result.block_id`` and record it.

    None when the staged repair now stands in place of the vendored copy, its
    override is in ``lock`` and in the product's saved lock, and the lock
    check passes with this block counted as overridden. Otherwise the reason,
    with the vendored copy, ``lock``, the saved lock and the proposals file as
    they were. Nothing raised inside escapes: it is undone and returned."""
    root = Path(workspace)
    block_id = result.block_id
    vendored = root.joinpath(*VENDOR_BLOCKS, block_id)
    backup = vendored.with_name(vendored.name + INSTALL_BACKUP_SUFFIX)
    trial = copy.deepcopy(dict(lock))
    before = copy.deepcopy(lock.get(OVERRIDES_KEY))
    had_overrides = OVERRIDES_KEY in lock
    saved_lock = _read_bytes(root / LOCK_FILE)
    filed_before = {row.get("proposal_id") for row in filed_proposals(root)}
    proposal_id: Optional[str] = None
    moved = False
    why: Optional[str] = None
    try:
        try:
            proposal = accept_repair(root, trial, result, proofs=proofs, defects=defects)
        except ProposalError as exc:
            why = str(exc)
        else:
            proposal_id = proposal.proposal_id
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(vendored, backup)
            moved = True
            shutil.copytree(
                root / REPAIR_DIR / block_id, vendored, ignore=shutil.ignore_patterns("__pycache__")
            )
            check = verify_vendored(root, trial, proofs=proofs)
            if check.ok and block_id in check.overridden:
                lock.setdefault(OVERRIDES_KEY, {})[block_id] = trial[OVERRIDES_KEY][block_id]
                save_lock(root, lock)
                shutil.rmtree(backup)
                return None
            refused = "; ".join(check.refused) or f"{block_id}: not counted as a recorded override"
            why = f"installed repair failed the lock check: {refused}"
    except Exception as exc:  # noqa: BLE001 -- any failure midway is undone and named
        why = f"install failed and was undone ({type(exc).__name__})"
    if had_overrides:
        lock[OVERRIDES_KEY] = before
    else:
        lock.pop(OVERRIDES_KEY, None)
    _undo_install(
        root,
        vendored,
        backup,
        moved=moved,
        saved_lock=saved_lock,
        proposal_id=proposal_id,
        filed_before=filed_before,
    )
    return why


def _lock_on_disk(root: Path) -> Dict[str, Any]:
    """The product's saved lock, or {} when there is none or it is not an object."""
    try:
        raw = json.loads((root / LOCK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _recover_one(root: Path, backup: Path) -> str:
    """F-3b: settle one backup an :func:`install_repair` killed midway left.

    The backup is restored when it is the copy the saved lock records as
    vendored (the override's ``replaces_hash``, else the block row's hash):
    whatever stands in its place is removed, and the override that now
    describes a repair no longer in place is dropped from the saved lock.
    When the backup is not that copy but the block is the override's repair,
    the install had finished and only the leftover is removed. Anything else
    is left as found, with the reason, for the lock check to refuse."""
    block_id = backup.name[: -len(INSTALL_BACKUP_SUFFIX)]
    vendored = backup.with_name(block_id)
    lock = _lock_on_disk(root)
    overrides = lock.get(OVERRIDES_KEY)
    override = overrides.get(block_id) if isinstance(overrides, dict) else None
    override = override if isinstance(override, dict) else {}
    entry = (lock.get("blocks") or {}).get(block_id)
    entry = entry if isinstance(entry, Mapping) else {}
    original = str(override.get("replaces_hash") or entry.get(VENDORED_HASH_KEY) or "")
    if original and tree_hash(backup) == original:
        if vendored.exists():
            shutil.rmtree(vendored)
        os.replace(backup, vendored)
        if override:
            del overrides[block_id]
            if not overrides:
                lock.pop(OVERRIDES_KEY)
            save_lock(root, lock)
        return f"{block_id}: a half-done install was undone; the vendored copy is back"
    if override and vendored.is_dir() and tree_hash(vendored) == override.get("override_hash"):
        shutil.rmtree(backup)
        return f"{block_id}: the install had finished; its leftover backup was removed"
    return f"{block_id}: a half-done install was left as found: its backup is not the copy the lock records"


def recover_installs(workspace: Path | str) -> List[str]:
    """F-3b, the start-up check: every backup a killed :func:`install_repair`
    left beside a vendored block is settled by :func:`_recover_one`, one
    line each, before anything reads the vendored blocks. A proposal row the
    install filed stays filed: its repair passed its exam either way."""
    root = Path(workspace)
    vendor = root.joinpath(*VENDOR_BLOCKS)
    if not vendor.is_dir():
        return []
    return [
        _recover_one(root, path)
        for path in sorted(vendor.iterdir())
        if path.is_dir() and path.name.endswith(INSTALL_BACKUP_SUFFIX)
    ]
