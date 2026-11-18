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

F-2 (grader 19:35Z): the proofs live in a :class:`ProofStore` that the build's
orchestrator (``RoleRunner``) opens. It is a Factory data directory that is
not inside the product, not on the build volume beside it, and not under any
root the coding agent's confinement may write. Every record in it is signed
(HMAC-SHA256) with a key only the Factory process holds
(:func:`take_proof_key` reads it once and removes it from the process
environment, so no child ever gets it), and every record names the build it
belongs to. A record that fails its signature or names another build reads as
absent. The same store keeps the hashes of the blocks as CLONER vendored them
(:func:`record_vendored`), so the lock check compares the bytes with what the
Factory cloned, never with a hash claimed in the product's own lock.

F-2 (grader 20:30Z): no key does not mean open. The store opens with or
without a key, and CLONER's record of the vendored blocks is always kept, so
any change to a vendored block is refused either way. With no key the record
carries a plain content digest (it is protected by where it sits: outside
every root the agent may write), no exam proof can be written or read, and so
an override is simply impossible.

The hash here walks every file on disk (not only git-tracked ones, unlike
``blocks_lock.block_content_hash``, which pins a committed Store tree): a file
planted inside a vendored block is a change and must be seen.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
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
#: The Factory's secret that signs every proof. It is read once, then removed
#: from the process environment so no child process inherits it.
PROOF_KEY_ENV = "FACTORY_EXAM_PROOF_KEY"
#: Where the Factory keeps proofs (default: under the Factory user's home).
PROOFS_DIR_ENV = "FACTORY_EXAM_PROOFS_DIR"
#: The record kind of an exam proof.
EXAM_KIND = "exam_proof"

_KEY_UNREAD: Any = object()
_process_key: Any = _KEY_UNREAD


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


@dataclass(frozen=True)
class ProofStore:
    """F-2: one build's Factory-held proofs, signed with the Factory's key."""

    root: Path
    build_id: str
    #: None: no exam can be proven in this build, so no override is possible.
    key: Optional[bytes] = field(default=None, repr=False)


def is_inside(path: Path | str, root: Path | str) -> bool:
    """True when ``path`` is ``root`` or lies under it."""
    inner = Path(path).resolve()
    outer = Path(root).resolve()
    return inner == outer or outer in inner.parents


def take_proof_key(environ: Optional[MutableMapping[str, str]] = None) -> Optional[bytes]:
    """The Factory's proof key. It is read once per process and then removed
    from ``environ`` (the process environment by default), so no child
    inherits it. None when it is not set."""
    global _process_key
    if _process_key is _KEY_UNREAD:
        env = os.environ if environ is None else environ
        raw = str(env.pop(PROOF_KEY_ENV, "") or "").strip()
        _process_key = raw.encode("utf-8") if raw else None
    return _process_key


def proofs_base(environ: Optional[Mapping[str, str]] = None) -> Path:
    """The Factory data directory that proofs are kept under."""
    env = os.environ if environ is None else environ
    raw = str(env.get(PROOFS_DIR_ENV) or "").strip()
    return Path(raw) if raw else Path.home() / ".cerebrum-factory" / "exam_proofs"


def placement_refusal(
    directory: Path | str, workspace: Path | str, rw_roots: Iterable[Path | str] = ()
) -> Optional[str]:
    """None when ``directory`` is out of the coding agent's reach for the
    build in ``workspace``. That means it is not on the build volume beside
    the product (this covers the product and every sibling build), and it is
    neither under nor above any root the agent's confinement may write.
    Otherwise, the reason it is refused."""
    volume = Path(workspace).resolve().parent
    if is_inside(directory, volume):
        return "exam proofs would sit on the build volume, beside the product"
    for root in rw_roots:
        if is_inside(directory, root) or is_inside(root, directory):
            return "exam proofs would sit where the coding agent may write"
    return None


def unusable_reason(exc: BaseException) -> str:
    """The plain reason a proofs directory that could not be resolved,
    created or written is refused: the error's type only, no path."""
    return f"the exam proofs directory is not usable ({type(exc).__name__})"


#: How long one probe of the proofs directory answers for: /ready is polled
#: every few seconds, and each probe writes to the directory's volume.
PROBE_CACHE_SECONDS = 60.0

_probe_cache: Dict[Tuple[str, str], Tuple[float, Optional[str]]] = {}


def probe_proofs_dir(
    builds_root: Path | str, environ: Optional[Mapping[str, str]] = None
) -> Optional[str]:
    """None when the proofs directory can serve a build under ``builds_root``;
    otherwise the plain reason it cannot. The directory is checked as the
    vendored-lock gate checks it: :func:`placement_refusal` for a build placed
    directly under ``builds_root``, against the same root list
    (``runner.agent_rw_roots_for``). It is then created and a probe file is
    written and removed in it. Any error at all is a reason (its type, no
    path), never an exception."""
    try:
        from app.factory.build.runner import agent_rw_roots_for

        workspace = Path(builds_root) / "build"
        base = proofs_base(environ).resolve()
        why = placement_refusal(base, workspace, agent_rw_roots_for(workspace))
        if why is not None:
            return why
        base.mkdir(parents=True, exist_ok=True)
        probe = base / f".probe-{os.getpid()}-{secrets.token_hex(4)}"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001 -- every failure is "not usable"
        return unusable_reason(exc)
    return None


def builds_unavailable_reason(
    builds_root: Path | str,
    environ: Optional[Mapping[str, str]] = None,
    *,
    now: Optional[float] = None,
) -> Optional[str]:
    """:func:`probe_proofs_dir`, answered from a result at most
    :data:`PROBE_CACHE_SECONDS` old for the same builds root and setting."""
    env = os.environ if environ is None else environ
    key = (str(builds_root), str(env.get(PROOFS_DIR_ENV) or ""))
    clock = time.monotonic() if now is None else now
    hit = _probe_cache.get(key)
    if hit is not None and 0 <= clock - hit[0] < PROBE_CACHE_SECONDS:
        return hit[1]
    reason = probe_proofs_dir(builds_root, environ)
    _probe_cache[key] = (clock, reason)
    return reason


def open_proof_store(
    build_id: str,
    workspace: Path | str,
    *,
    key: Optional[bytes],
    base: Path | str,
    rw_roots: Iterable[Path | str] = (),
) -> ProofStore:
    """The proof store of ``build_id`` under ``base``, with or without a key
    (with none, overrides are impossible but the vendored record is still
    kept). Raises OverrideError when there is no build id or the place is one
    the coding agent could reach."""
    if not str(build_id or "").strip():
        raise OverrideError("a proof store needs the build id it belongs to")
    root = Path(base) / hashlib.sha256(build_id.encode("utf-8")).hexdigest()[:32]
    why = placement_refusal(root, workspace, rw_roots)
    if why is not None:
        raise OverrideError(why)
    return ProofStore(root=root.resolve(), build_id=build_id, key=bytes(key) if key else None)


def _signature(key: Optional[bytes], payload: Mapping[str, Any]) -> str:
    """HMAC-SHA256 with the Factory's key; a plain digest when there is none."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    if not key:
        return "digest:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


def _write_signed(store: ProofStore, name: str, kind: str, payload: Mapping[str, Any]) -> Path:
    signed = {**payload, "kind": kind, "build_id": store.build_id}
    path = store.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    body = {"payload": signed, "signature": _signature(store.key, signed)}
    tmp.write_text(json.dumps(body, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _read_signed(store: ProofStore, name: str, kind: str) -> Optional[Mapping[str, Any]]:
    """The record's payload when the Factory signed it and it is of ``kind``
    for this build; None otherwise."""
    try:
        raw = json.loads((store.root / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    payload = raw.get("payload") if isinstance(raw, Mapping) else None
    if not isinstance(payload, Mapping):
        return None
    if not hmac.compare_digest(str(raw.get("signature") or ""), _signature(store.key, payload)):
        return None
    if payload.get("kind") != kind or payload.get("build_id") != store.build_id:
        return None
    return payload


def _proof_name(exam_id: str) -> str:
    return hashlib.sha256(exam_id.encode("utf-8")).hexdigest() + PROOF_SUFFIX


def write_proof(store: ProofStore, proof: ExamProof) -> Path:
    """Sign ``proof`` for this build, write it atomically, and return its path.
    Refused when the Factory holds no key: an unsigned proof proves nothing."""
    if not store.key:
        raise OverrideError(f"{PROOF_KEY_ENV} is not set: the Factory can prove no exam")
    if not proof.exam_id:
        raise OverrideError(f"exam proof for {proof.block_id!r} has no exam id")
    return _write_signed(store, _proof_name(proof.exam_id), EXAM_KIND, asdict(proof))


def read_proof(store: Optional[ProofStore], exam_id: str) -> Optional[ExamProof]:
    """The Factory-held, signed proof of ``exam_id`` for this build. None when
    it is absent, unreadable, not signed by the Factory, or from another build,
    and always when the Factory holds no key."""
    if store is None or not store.key or not exam_id:
        return None
    raw = _read_signed(store, _proof_name(exam_id), EXAM_KIND)
    if raw is None or raw.get("exam_id") != exam_id:
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


def _override_refusal(block_id: str, actual: str, recorded: str, raw: Any) -> Optional[str]:
    """None when ``raw`` is a valid recorded override for these bytes."""
    if not isinstance(raw, Mapping):
        return f"{block_id}: differs from the vendored Store copy and no override is recorded"
    record = OverrideRecord.from_mapping(raw)
    if not record.exam_passed:
        return f"{block_id}: recorded override did not pass its exam"
    if record.replaces_hash != recorded:
        return f"{block_id}: recorded override replaces a different copy than the one vendored"
    if record.override_hash != actual:
        return f"{block_id}: differs from both the vendored copy and its recorded override"
    return None


def verify_vendored(root: Path | str, lock: Mapping[str, Any]) -> OverrideCheck:
    """R3, the lock check: refuse any difference from the vendored Store copy
    that is not a recorded override which passed its exam."""
    root = Path(root)
    entries = lock.get("blocks") or {}
    overrides = lock.get(OVERRIDES_KEY) or {}
    unchanged: List[str] = []
    overridden: List[str] = []
    refused: List[str] = []
    for block_id in sorted(entries):
        entry = entries[block_id]
        if not isinstance(entry, Mapping) or not entry.get("path"):
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
        why = _override_refusal(block_id, actual, recorded, overrides.get(block_id))
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
