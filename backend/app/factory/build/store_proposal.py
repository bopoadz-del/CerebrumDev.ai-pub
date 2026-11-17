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
exam's proof from the Factory-held directory outside the product (written by
the repairer, see :func:`store_override.write_proof`) and accepts only when it
shows this block passing on exactly the staged bytes; the block must be
vendored at ``vendor/blocks/<id>``. Every check runs before anything is
written; the proposal is filed (atomically) first and only then is the
override written into the lock, so a refusal or a failed filing leaves the
lock untouched.
A corrupt proposals line is skipped, not fatal.

F-3: :func:`install_repair` is the installer :func:`store_repair.settle`
calls. It accepts the repair (above) against a trial copy of the lock, puts
the staged repair in place of the vendored copy and re-runs the lock check on
the product as it now stands. Only when that check passes, with this block
counted as a recorded override, is the override written into the caller's
lock. Otherwise the vendored copy is restored, a proposal row this call filed
is withdrawn and the reason is returned.
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
    StoreDefect,
    is_inside,
    proof_refusal,
    read_proof,
    record_override,
    tree_hash,
    vendored_at,
    vendored_path,
    verify_vendored,
)
from app.factory.build.store_repair import RepairResult
from app.factory.build.store_repair_exam import REPAIR_DIR

#: Where the proposals are filed inside the product (spec design table, R6).
PROPOSALS_FILE = ("docs", "store_upgrade_proposals.jsonl")
#: A Store block's directory in the Store (the Carry-Back intake's block path).
STORE_BLOCK_DIR = "block_registry"
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


def _write_rows(workspace: Path | str, rows: Sequence[Mapping[str, Any]]) -> None:
    path = proposals_path(workspace)
    text = "".join(json.dumps(dict(r), sort_keys=True, default=str) + "\n" for r in rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def withdraw_proposal(workspace: Path | str, proposal_id: str) -> bool:
    """F-3: remove the row ``proposal_id``. False when it is not filed."""
    rows = filed_proposals(workspace)
    kept = [row for row in rows if row.get("proposal_id") != proposal_id]
    if len(kept) == len(rows):
        return False
    _write_rows(workspace, kept)
    return True


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


def accept_repair(
    workspace: Path | str,
    lock: MutableMapping[str, Any],
    result: RepairResult,
    *,
    build_id: str,
    proofs_dir: Path | str,
    defects: Sequence[StoreDefect] = (),
) -> StoreProposal:
    """R3 + R6: record the passed repair as the block's override and file its
    upgrade proposal, in one step. A repair that did not pass its exam, as the
    Factory-held proof in ``proofs_dir`` shows, is neither recorded nor filed."""
    if result.passed is not True or not result.exam_id:
        raise ProposalError(f"repair of {result.block_id!r} did not pass its exam: nothing to file")
    root = Path(workspace)
    if is_inside(proofs_dir, root):
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
    replaces = str(entry.get(VENDORED_HASH_KEY) or "")
    if not replaces:
        raise ProposalError(f"repair of {block_id!r}: no recorded hash of the vendored copy")
    override_hash = tree_hash(staging)
    proof = read_proof(proofs_dir, result.exam_id)
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
        build_id=str(build_id or ""),
        patch=store_patch(root.joinpath(*VENDOR_BLOCKS, block_id), staging, block_id),
        defects=_defect_rows(defects),
    )
    file_proposal(root, proposal)
    record_override(lock, record)
    return proposal


def install_repair(
    workspace: Path | str,
    lock: MutableMapping[str, Any],
    result: RepairResult,
    *,
    build_id: str,
    proofs_dir: Path | str,
    defects: Sequence[StoreDefect] = (),
) -> Optional[str]:
    """F-3: install the passed repair of ``result.block_id`` and record it.

    None when the staged repair now stands in place of the vendored copy, its
    override is in ``lock`` and the lock check passes with this block counted
    as overridden. Otherwise the reason, with the vendored copy, ``lock`` and
    the proposals file as they were."""
    root = Path(workspace)
    block_id = result.block_id
    trial = copy.deepcopy(dict(lock))
    filed_before = {row.get("proposal_id") for row in filed_proposals(root)}
    try:
        proposal = accept_repair(
            root, trial, result, build_id=build_id, proofs_dir=proofs_dir, defects=defects
        )
    except ProposalError as exc:
        return str(exc)
    vendored = root.joinpath(*VENDOR_BLOCKS, block_id)
    backup = vendored.with_name(vendored.name + INSTALL_BACKUP_SUFFIX)
    if backup.exists():
        shutil.rmtree(backup)
    os.replace(vendored, backup)
    shutil.copytree(
        root / REPAIR_DIR / block_id, vendored, ignore=shutil.ignore_patterns("__pycache__")
    )
    check = verify_vendored(root, trial, proofs_dir=proofs_dir)
    if not check.ok or block_id not in check.overridden:
        shutil.rmtree(vendored)
        os.replace(backup, vendored)
        if proposal.proposal_id not in filed_before:
            withdraw_proposal(root, proposal.proposal_id)
        why = "; ".join(check.refused) or f"{block_id}: not counted as a recorded override"
        return f"installed repair failed the lock check: {why}"
    shutil.rmtree(backup)
    lock.setdefault(OVERRIDES_KEY, {})[block_id] = trial[OVERRIDES_KEY][block_id]
    return None
