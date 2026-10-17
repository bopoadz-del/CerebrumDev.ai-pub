"""Factory pin of the Cerebrum-Blocks store: blocks.lock.json.

Store drift cannot silently change a Factory build. Every block the Factory
consumes is recorded with id, version, and a content hash generated from the
store tree. Resolve goes THROUGH the lock: an unlocked or hash-mismatched
block is a hard failure that names the block and both hashes.

Refresh the pin deliberately::

    python -m app.factory.cli update-lock --blocks-root "$CEREBRUM_BLOCKS_ROOT"
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

SCHEMA = "factory.blocks.lock.v1"
STORE_REPO = "https://github.com/bopoadz-del/Cerebrum-Blocks"
LOCK_REGENERATE_HINT = (
    'Regenerate with: python -m app.factory.cli update-lock '
    '--blocks-root "$CEREBRUM_BLOCKS_ROOT"'
)


class BlocksLockError(Exception):
    """Unlocked or hash-mismatched block — never a warning."""


def default_lock_path() -> Path:
    from app.factory.paths import factory_repo_root

    override = (os.getenv("FACTORY_BLOCKS_LOCK") or "").strip()
    if override:
        return Path(override).resolve()
    return factory_repo_root() / "blocks.lock.json"


def vendor_mirror_root() -> Path:
    return Path(__file__).resolve().parent / "vendor_blocks_mirror"


def consumed_block_ids(factory_shelf: Optional[Path] = None) -> list[str]:
    from app.factory.dual_registry import load_factory_shelf

    return sorted(load_factory_shelf(factory_shelf))


def _tracked_relpaths(source: Path) -> Optional[List[str]]:
    """Repo-relative posix paths under ``source``, or None when not a git tree.

    The lock must describe the COMMITTED store tree at the recorded sha —
    untracked working-tree files must never leak into a content hash.
    """
    root = Path(source)
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "."],
            cwd=root,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    rel = [p for p in listed.stdout.decode("utf-8", "replace").split("\0") if p]
    # An empty list is meaningful: a git tree whose block dir has no
    # committed files (e.g. untracked working-tree leftovers). None is
    # reserved for "not a git tree at all".
    return rel


def block_content_hash(source: Path) -> str:
    """Stable sha256 of a block directory (path + bytes, skip __pycache__).

    Deterministic across platforms: only git-tracked files count (the
    lock pins the committed tree at the recorded store sha), files are
    ordered by posix relative path (WindowsPath sorts
    case-insensitively, PosixPath case-sensitively), and bytes are
    normalized CRLF → LF so a core.autocrlf working tree hashes like
    the LF blobs CI checks out.
    """
    digest = hashlib.sha256()
    root = Path(source)
    tracked = _tracked_relpaths(root)
    if tracked is not None:
        rels = [p for p in tracked if "__pycache__" not in p.split("/")]
    else:
        rels = [
            p.relative_to(root).as_posix()
            for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix())
            if p.is_file() and "__pycache__" not in p.parts
        ]
    for rel in sorted(rels):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / rel).read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _read_block_version(block_dir: Path, block_id: str) -> str:
    meta = block_dir / "block.json"
    if not meta.is_file():
        return "0"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "0"
    if isinstance(data, dict) and data.get("version") is not None:
        return str(data["version"])
    return "0"


def _git_head(repo: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = (result.stdout or "").strip()
    return sha or None


def _block_dir_in_store(blocks_root: Path, block_id: str) -> Optional[Path]:
    candidate = Path(blocks_root) / "block_registry" / block_id
    if not candidate.is_dir():
        return None
    tracked = _tracked_relpaths(candidate)
    if tracked is not None:
        # The lock must pin the COMMITTED store tree; an untracked
        # block dir in the working tree is not part of the pinned sha.
        if any(p in ("block.py", "block.json") for p in tracked):
            return candidate
        return None
    if (candidate / "block.py").is_file() or (candidate / "block.json").is_file():
        return candidate
    return None


def _block_dir_in_mirror(block_id: str) -> Optional[Path]:
    candidate = vendor_mirror_root() / block_id
    if (candidate / "block.py").is_file() or (candidate / "block.json").is_file():
        return candidate
    return None


def generate_lock(
    blocks_root: Optional[Path] = None,
    *,
    consumed_ids: Optional[Sequence[str]] = None,
    store_sha: Optional[str] = None,
    include_vendor_mirror: bool = True,
) -> Dict[str, Any]:
    """Build a lock dict from the store tree. Mechanical — do not hand-edit."""
    ids = list(consumed_ids) if consumed_ids is not None else consumed_block_ids()
    root = Path(blocks_root) if blocks_root else None
    sha = store_sha or (_git_head(root) if root else None) or "unknown"
    blocks: Dict[str, Any] = {}
    missing: list[str] = []
    for bid in ids:
        source = _block_dir_in_store(root, bid) if root else None
        origin = "cerebrum-blocks"
        if source is None and include_vendor_mirror:
            source = _block_dir_in_mirror(bid)
            origin = "factory-vendor-mirror"
        if source is None:
            missing.append(bid)
            continue
        blocks[bid] = {
            "id": bid,
            "version": _read_block_version(source, bid),
            "content_hash": block_content_hash(source),
            "source": origin,
        }
    if missing:
        raise BlocksLockError(
            "BLOCKS_LOCK: cannot generate lock; no source for consumed "
            "block(s): " + ", ".join(missing)
        )
    return {
        "schema": SCHEMA,
        "store": {"repo": STORE_REPO, "sha": sha},
        "blocks": blocks,
    }


def write_lock(path: Path, lock: Mapping[str, Any]) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def load_lock(path: Optional[Path] = None) -> Dict[str, Any]:
    dest = Path(path) if path is not None else default_lock_path()
    if not dest.is_file():
        raise BlocksLockError(f"BLOCKS_LOCK: lock file missing at {dest}")
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BlocksLockError(f"BLOCKS_LOCK: lock file unreadable at {dest}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), dict):
        raise BlocksLockError(f"BLOCKS_LOCK: invalid lock schema at {dest}")
    return data


def load_lock_if_present(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    dest = Path(path) if path is not None else default_lock_path()
    if not dest.is_file():
        return None
    return load_lock(dest)


def top_up_from_store(
    lock: Optional[Mapping[str, Any]],
    blocks_root: Optional[Path] = None,
    *,
    consumed: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Lock the Store blocks this lock has never seen. Existing entries stand.

    The committed lock is a SNAPSHOT of integrity, not the inventory. The
    inventory is the Store's: the Factory reads what the Store publishes
    (``dual_registry.shelf_from_store``) and must be able to build from it.
    Treating the committed file as the membership list meant every block the
    Store published was "missing from the lock" until somebody regenerated and
    committed it -- live, thirteen certified blocks (the marketplace_ops pack
    and ``vendor_catalog``) were unbuildable for exactly that reason.

    So a consumed block the lock does not carry is hashed from the Store and
    added now. A block the lock DOES carry is never touched, so a tree that
    changed under a recorded hash still fails hard: tamper detection is
    unchanged, only the "I have not met this block before" case moves.
    """
    if lock is None:
        return None
    merged = dict(lock)
    blocks = dict(merged.get("blocks") or {})
    try:
        ids = list(consumed) if consumed is not None else consumed_block_ids()
    except Exception:  # noqa: BLE001 -- an unreadable shelf tops up nothing
        return merged
    unseen = [bid for bid in ids if bid not in blocks]
    if not unseen:
        return merged
    root = Path(blocks_root) if blocks_root else None
    if root is None:
        try:
            from app.factory.blocks_source import resolve_blocks_root

            resolved = resolve_blocks_root()
            root = Path(resolved) if resolved else None
        except Exception:  # noqa: BLE001 -- no Store, nothing to top up from
            root = None
    added = []
    for bid in unseen:
        source = _block_dir_in_store(root, bid) if root else None
        origin = "cerebrum-blocks"
        if source is None:
            source = _block_dir_in_mirror(bid)
            origin = "factory-vendor-mirror"
        if source is None:
            # Still absent: enforce_store_lock reports it, naming the block.
            continue
        blocks[bid] = {
            "id": bid,
            "version": _read_block_version(source, bid),
            "content_hash": block_content_hash(source),
            "source": origin,
        }
        added.append(bid)
    if added:
        merged["blocks"] = blocks
        merged["topped_up"] = sorted(added)
    return merged


def resolve_lock(
    explicit: Optional[Mapping[str, Any]] = None,
    *,
    lock_path: Optional[Path] = None,
    blocks_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    if explicit is not None:
        return dict(explicit)
    if lock_path is not None:
        return top_up_from_store(load_lock(lock_path), blocks_root)
    return top_up_from_store(load_lock_if_present(), blocks_root)


def assert_block_matches_lock(
    block_id: str,
    source_dir: Path,
    lock: Optional[Mapping[str, Any]],
) -> str:
    """Hard-fail unless ``source_dir`` matches the lock entry for ``block_id``.

    Returns the verified content hash. Never warns, never falls through.
    """
    actual = block_content_hash(Path(source_dir))
    entries = (lock or {}).get("blocks") if isinstance(lock, Mapping) else None
    if not isinstance(entries, dict) or block_id not in entries:
        raise BlocksLockError(
            f"BLOCKS_LOCK: unlocked block {block_id!r} is not in the lock "
            f"(computed hash {actual}). {LOCK_REGENERATE_HINT}"
        )
    record = entries[block_id]
    recorded = ""
    if isinstance(record, Mapping):
        recorded = str(record.get("content_hash") or "")
    if not recorded:
        raise BlocksLockError(
            f"BLOCKS_LOCK: unlocked block {block_id!r} has no content_hash "
            f"in the lock (computed hash {actual}). {LOCK_REGENERATE_HINT}"
        )
    if recorded != actual:
        raise BlocksLockError(
            f"BLOCKS_LOCK: hash mismatch for block {block_id!r}: "
            f"lock={recorded} store={actual}. {LOCK_REGENERATE_HINT}"
        )
    return actual


def is_store_source(source: Path, blocks_root: Optional[Path]) -> bool:
    if not blocks_root:
        return False
    try:
        source.resolve().relative_to(Path(blocks_root).resolve())
    except (ValueError, OSError):
        return False
    return True


def enforce_store_lock(
    block_id: str,
    source: Path,
    blocks_root: Optional[Path],
    lock: Optional[Mapping[str, Any]],
    *,
    consumed: Optional[Iterable[str]] = None,
) -> None:
    """Enforce the pin when the source is a Store checkout of a consumed block."""
    if not is_store_source(source, blocks_root):
        return
    wanted = set(consumed) if consumed is not None else set(consumed_block_ids())
    if block_id not in wanted:
        return
    if lock is None:
        lock = resolve_lock()
    if lock is None:
        raise BlocksLockError(
            f"BLOCKS_LOCK: lock file missing at {default_lock_path()}. "
            f"Cannot clone store-sourced block {block_id!r} without a pin. "
            f"{LOCK_REGENERATE_HINT}"
        )
    assert_block_matches_lock(block_id, source, lock)
