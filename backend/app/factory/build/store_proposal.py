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
override written into the lock, so a refusal or a failed filing leaves the
lock untouched; if writing the override fails after filing, the row this call
filed is withdrawn.
A corrupt proposals line is skipped, not fatal.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from app.factory.build.store_override import (
    VENDOR_BLOCKS,
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
)
from app.factory.build.store_repair import RepairResult
from app.factory.build.store_repair_exam import REPAIR_DIR

#: Where the proposals are filed inside the product (spec design table, R6).
PROPOSALS_FILE = ("docs", "store_upgrade_proposals.jsonl")
#: A Store block's directory in the Store (the Carry-Back intake's block path).
STORE_BLOCK_DIR = "block_registry"
#: The reason an accepted repair's override is recorded with.
REPAIR_REASON = "Store block defect repaired under its exam"


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

    Atomic in both directions: a refusal or a failed filing leaves the lock
    untouched, and a failure in recording the override after the proposal was
    filed withdraws the row this call filed."""
    if result.passed is not True or not result.exam_id:
        raise ProposalError(f"repair of {result.block_id!r} did not pass its exam: nothing to file")
    if proofs is None:
        raise ProposalError("the Factory holds no proof key: nothing to file")
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
    filed = file_proposal(root, proposal)
    try:
        record_override(lock, record, vendored_hash=replaces)
    except BaseException:
        if filed:
            withdraw_proposal(root, proposal.proposal_id)
        raise
    return proposal
