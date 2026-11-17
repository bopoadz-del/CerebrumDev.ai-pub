"""R1 + R3 (Advisor 12:13Z): a misbehaving Store block is named, and a
writer repair of it ships only as a RECORDED local override.

Two pure pieces, not yet wired into the runner (K-1 of the spec's section K):

* :func:`store_defects` -- R1. A grader/gate finding whose file lies inside a
  vendored block (``vendor/blocks/<id>/...``) is a STORE DEFECT of that block:
  the block id is read from the path itself, never from a list of names. The
  defect carries the failing input and the kind of failure the finding
  states, and nothing else.

* :func:`verify_vendored` -- R3, the lock check. For every vendored block the
  product lock names, the bytes on disk must equal the vendored copy CLONER
  recorded, OR equal a recorded override of that one block that names the
  copy it replaces and passed its exam. Every other difference is refused,
  naming the block. An override is recorded only through
  :func:`record_override`, which refuses one that did not pass its exam.

F-2: the lock alone is not proof. The lock lives in the product tree, which
the writer can edit, so a hand-written override row could claim an exam. The
exam's proof (exam id, block, hash of the tree the exam examined, verdict) is
written by the Factory with :func:`write_proof` into a directory it holds
OUTSIDE the product tree, and both acceptance and the lock check read it with
:func:`read_proof`: an override counts only when that proof shows this block
passing on exactly these bytes. A block is vendored only at
``vendor/blocks/<id>``.

The hash here walks every file on disk (not only git-tracked ones, unlike
``blocks_lock.block_content_hash``, which pins a committed Store tree): a file
planted inside a vendored block is a change and must be seen.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

#: Where CLONER vendors a Store block inside a product (roles_handlers CLONER).
VENDOR_BLOCKS = ("vendor", "blocks")
#: Lock section holding the recorded overrides, keyed by block id.
OVERRIDES_KEY = "overrides"
#: The lock entry field holding the hash of the vendored copy as CLONER left it.
VENDORED_HASH_KEY = "vendored_content_hash"
#: Reason a failed repair is reported with (R4).
REPAIR_FAILED_REASON = "Store block defect, repair failed"
#: File suffix of one Factory-held exam proof (F-2).
PROOF_SUFFIX = ".exam.json"


class OverrideError(ValueError):
    """An override that may not be recorded (it did not pass its exam)."""


@dataclass(frozen=True)
class StoreDefect:
    """R1: one defect of one Store block, as the finding stated it."""

    block_id: str
    path: str
    kind: str
    failing_input: Any = None


@dataclass(frozen=True)
class OverrideRecord:
    """R3: which block, which vendored copy it replaces, why, and its exam."""

    block_id: str
    replaces_hash: str
    override_hash: str
    reason: str
    exam_id: str
    exam_passed: bool
    store_commit: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "OverrideRecord":
        return cls(
            block_id=str(raw.get("block_id") or ""),
            replaces_hash=str(raw.get("replaces_hash") or ""),
            override_hash=str(raw.get("override_hash") or ""),
            reason=str(raw.get("reason") or ""),
            exam_id=str(raw.get("exam_id") or ""),
            exam_passed=raw.get("exam_passed") is True,
            store_commit=str(raw.get("store_commit") or ""),
        )


@dataclass(frozen=True)
class OverrideCheck:
    """The lock check's verdict over every vendored block."""

    ok: bool
    unchanged: Tuple[str, ...] = ()
    overridden: Tuple[str, ...] = ()
    refused: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ExamProof:
    """F-2: what the Factory saw when it ran a block's exam."""

    exam_id: str
    block_id: str
    examined_hash: str
    passed: bool
    score: Optional[float] = None


def is_inside(path: Path | str, root: Path | str) -> bool:
    """True when ``path`` is ``root`` or lies under it."""
    inner = Path(path).resolve()
    outer = Path(root).resolve()
    return inner == outer or outer in inner.parents


def _proof_path(proofs_dir: Path | str, exam_id: str) -> Path:
    name = hashlib.sha256(exam_id.encode("utf-8")).hexdigest()
    return Path(proofs_dir) / (name + PROOF_SUFFIX)


def write_proof(proofs_dir: Path | str, proof: ExamProof) -> Path:
    """Write ``proof`` atomically (temp file, then rename) and return its path."""
    if not proof.exam_id:
        raise OverrideError(f"exam proof for {proof.block_id!r} has no exam id")
    path = _proof_path(proofs_dir, proof.exam_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(proof), sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_proof(proofs_dir: Path | str, exam_id: str) -> Optional[ExamProof]:
    """The Factory-held proof of ``exam_id``; None when absent or unreadable."""
    if not exam_id:
        return None
    try:
        raw = json.loads(_proof_path(proofs_dir, exam_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping) or raw.get("exam_id") != exam_id:
        return None
    score = raw.get("score")
    return ExamProof(
        exam_id=exam_id,
        block_id=str(raw.get("block_id") or ""),
        examined_hash=str(raw.get("examined_hash") or ""),
        passed=raw.get("passed") is True,
        score=float(score) if isinstance(score, (int, float)) else None,
    )


def proof_refusal(proof: Optional[ExamProof], block_id: str, actual: str) -> Optional[str]:
    """None when the Factory-held ``proof`` shows ``block_id`` passing its
    exam on a tree whose hash is ``actual``; otherwise why not."""
    if proof is None:
        return f"{block_id}: no Factory-held proof of its exam"
    if proof.block_id != block_id or proof.passed is not True:
        return f"{block_id}: the Factory-held proof does not show it passing its exam"
    if proof.examined_hash != actual:
        return f"{block_id}: these bytes are not the tree its exam examined"
    return None


def vendored_path(block_id: str) -> str:
    """The one product-relative path a block may be vendored at."""
    return "/".join(VENDOR_BLOCKS + (block_id,))


def vendored_at(path: Any, block_id: str) -> bool:
    """True when the lock's ``path`` is the one path ``block_id`` may have."""
    return "/".join(_rel_parts(path)) == vendored_path(block_id)


def _rel_parts(path: Any) -> Tuple[str, ...]:
    text = str(path or "").replace("\\", "/")
    return tuple(p for p in text.split("/") if p and p != ".")


def block_of_path(path: Any) -> Optional[str]:
    """The block id a product-relative path lies inside, or None."""
    parts = _rel_parts(path)
    n = len(VENDOR_BLOCKS)
    if len(parts) > n and parts[:n] == VENDOR_BLOCKS:
        return parts[n]
    return None


def _finding_path(finding: Mapping[str, Any]) -> str:
    for key in ("file", "path"):
        value = finding.get(key)
        if value:
            return str(value)
    return ""


def store_defects(findings: Iterable[Mapping[str, Any]]) -> List[StoreDefect]:
    """R1: every finding that points inside a vendored block, named by block.

    One defect per (block, kind, failing input); findings outside a vendored
    block are not Store defects and are left to their own owner."""
    out: List[StoreDefect] = []
    seen = set()
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        path = _finding_path(finding)
        block_id = block_of_path(path)
        if block_id is None:
            continue
        kind = str(finding.get("kind") or "unknown")
        failing_input = finding.get("input")
        key = (block_id, kind, repr(failing_input))
        if key in seen:
            continue
        seen.add(key)
        out.append(StoreDefect(block_id, "/".join(_rel_parts(path)), kind, failing_input))
    return out


def tree_hash(block_dir: Path) -> str:
    """sha256 over every file under ``block_dir`` (posix path + LF bytes),
    skipping only ``__pycache__``."""
    digest = hashlib.sha256()
    root = Path(block_dir)
    files = sorted(
        (p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts),
        key=lambda q: q.relative_to(root).as_posix(),
    )
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def record_override(lock: MutableMapping[str, Any], record: OverrideRecord) -> Dict[str, Any]:
    """R3: write ``record`` into the lock. Only a passed exam is recorded, and
    only over a block the lock vendored, replacing the copy it recorded."""
    if not record.exam_passed or not record.exam_id:
        raise OverrideError(
            f"override of {record.block_id!r} not recorded: it did not pass its exam"
        )
    entry = (lock.get("blocks") or {}).get(record.block_id)
    if not isinstance(entry, Mapping):
        raise OverrideError(f"override of {record.block_id!r}: the lock vendored no such block")
    if str(entry.get(VENDORED_HASH_KEY) or "") != record.replaces_hash:
        raise OverrideError(
            f"override of {record.block_id!r} does not name the vendored copy it replaces"
        )
    overrides = lock.setdefault(OVERRIDES_KEY, {})
    overrides[record.block_id] = record.to_dict()
    return overrides[record.block_id]


def _override_refusal(
    block_id: str, actual: str, recorded: str, raw: Any, proofs_dir: Path | str
) -> Optional[str]:
    """None when ``raw`` is a valid recorded override for these bytes."""
    if not isinstance(raw, Mapping):
        return f"{block_id}: differs from the vendored Store copy and no override is recorded"
    record = OverrideRecord.from_mapping(raw)
    if not record.exam_passed:
        return f"{block_id}: recorded override did not pass its exam"
    if not record.exam_id:
        return f"{block_id}: recorded override names no exam"
    if record.replaces_hash != recorded:
        return f"{block_id}: recorded override replaces a different copy than the one vendored"
    if record.override_hash != actual:
        return f"{block_id}: differs from both the vendored copy and its recorded override"
    return proof_refusal(read_proof(proofs_dir, record.exam_id), block_id, actual)


def verify_vendored(
    root: Path | str, lock: Mapping[str, Any], *, proofs_dir: Path | str
) -> OverrideCheck:
    """R3, the lock check: refuse any difference from the vendored Store copy
    that is not a recorded override which passed its exam, as the
    Factory-held proof in ``proofs_dir`` (outside the product) shows."""
    root = Path(root)
    if is_inside(proofs_dir, root):
        return OverrideCheck(ok=False, refused=("exam proofs are held inside the product tree",))
    entries = lock.get("blocks") or {}
    overrides = lock.get(OVERRIDES_KEY) or {}
    unchanged: List[str] = []
    overridden: List[str] = []
    refused: List[str] = []
    for block_id in sorted(entries):
        entry = entries[block_id]
        if not isinstance(entry, Mapping) or not entry.get("path"):
            continue
        if not vendored_at(entry["path"], block_id):
            refused.append(f"{block_id}: not vendored at {vendored_path(block_id)}")
            continue
        block_dir = root / str(entry["path"])
        recorded = str(entry.get(VENDORED_HASH_KEY) or "")
        if not recorded:
            refused.append(f"{block_id}: no recorded hash of the vendored copy")
            continue
        if not block_dir.is_dir():
            refused.append(f"{block_id}: vendored copy is missing")
            continue
        actual = tree_hash(block_dir)
        if actual == recorded:
            unchanged.append(block_id)
            continue
        why = _override_refusal(block_id, actual, recorded, overrides.get(block_id), proofs_dir)
        if why is None:
            overridden.append(block_id)
        else:
            refused.append(why)
    for block_id in sorted(set(overrides) - set(entries)):
        refused.append(f"{block_id}: override recorded for a block the lock never vendored")
    return OverrideCheck(
        ok=not refused,
        unchanged=tuple(unchanged),
        overridden=tuple(overridden),
        refused=tuple(refused),
    )
