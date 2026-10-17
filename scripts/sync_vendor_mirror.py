#!/usr/bin/env python3
"""Re-vendor lock-pinned Store block dirs into vendor_blocks_mirror.

Pulls the complete ``block_registry/<id>/`` tree from Cerebrum-Blocks at the
lock pin (default ``store.sha``, else per-block SHA if present) into
``vendor_blocks_mirror/<id>/``. Also copies sibling ``<id>_block.py`` (and
the class module for knowledge) NEXT TO the hashed directory so lock hashes
stay exact.

Estate-only lock entries (``source: factory-vendor-mirror``, not in the
Store) are restored from the lock-writing commit so mirror==lock without
editing the lock.

Usage:
  python3 scripts/sync_vendor_mirror.py
  python3 scripts/sync_vendor_mirror.py --only document_engine,knowledge
  python3 scripts/sync_vendor_mirror.py --blocks-root /path/to/Cerebrum-Blocks
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.factory.blocks_lock import (  # noqa: E402
    STORE_REPO,
    block_content_hash,
    default_lock_path,
    vendor_mirror_root,
)

STORE_REPO_GIT = STORE_REPO if STORE_REPO.endswith(".git") else STORE_REPO + ".git"
# #401 wrote the factory-vendor-mirror hashes still pinned in the lock.
LOCK_ERA_MIRROR_SHA = "1f50bd9"


def _run(cmd: Sequence[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_lock(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), dict):
        raise SystemExit(f"sync_vendor_mirror: invalid lock at {path}")
    return data


def _pin_sha(lock: dict, rec: dict) -> str:
    per = str(rec.get("sha") or rec.get("store_sha") or "").strip()
    if per:
        return per
    return str((lock.get("store") or {}).get("sha") or "").strip()


def ensure_store(sha: str, *, blocks_root: Optional[Path] = None) -> Path:
    """Read-only Store checkout at *sha*. Never writes to a sibling clone."""
    if blocks_root is not None:
        root = Path(blocks_root)
        if not (root / "block_registry").is_dir():
            raise SystemExit(f"sync_vendor_mirror: not a Store checkout: {root}")
        return root
    cache = Path(tempfile.gettempdir()) / f"cerebrum-blocks-{sha}"
    if (cache / "block_registry").is_dir() and (cache / ".git").is_dir():
        head = _run(["git", "rev-parse", "HEAD"], cwd=cache)
        if head.returncode == 0 and (head.stdout or "").strip().startswith(sha[:7]):
            return cache
    if cache.exists():
        shutil.rmtree(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    init = _run(["git", "init", str(cache)])
    if init.returncode != 0:
        raise SystemExit(f"sync_vendor_mirror: git init failed: {init.stderr}")
    remote = _run(["git", "remote", "add", "origin", STORE_REPO_GIT], cwd=cache)
    if remote.returncode != 0:
        raise SystemExit(f"sync_vendor_mirror: git remote failed: {remote.stderr}")
    fetch = _run(["git", "fetch", "--depth", "1", "origin", sha], cwd=cache)
    if fetch.returncode != 0:
        raise SystemExit(
            f"sync_vendor_mirror: cannot fetch Store {sha}: {fetch.stderr}"
        )
    checkout = _run(["git", "checkout", "FETCH_HEAD"], cwd=cache)
    if checkout.returncode != 0:
        raise SystemExit(f"sync_vendor_mirror: checkout failed: {checkout.stderr}")
    return cache


def _copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
    )


def _copy_siblings(store: Path, block_id: str, mirror_root: Path) -> list[str]:
    """Copy ``<id>_block.py`` (and knowledge class module) next to the hashed dir."""
    copied: list[str] = []
    dest_dir = mirror_root
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        store / "app" / "blocks" / f"{block_id}_block.py",
        store / "block_registry" / f"{block_id}_block.py",
    ]
    if block_id == "knowledge":
        # Store ships KnowledgeBlock in knowledge.py, not knowledge_block.py.
        candidates.append(store / "app" / "blocks" / "knowledge.py")
    seen: set[str] = set()
    for src in candidates:
        if not src.is_file():
            continue
        name = src.name
        if block_id == "knowledge" and name == "knowledge.py":
            name = "knowledge_block.py"
        if name in seen:
            continue
        seen.add(name)
        shutil.copy2(src, dest_dir / name)
        copied.append(name)
    return copied


def _restore_lock_era_mirror(block_id: str, dest: Path, repo: Path) -> None:
    """Estate-only: restore the #401 mirror tree that the lock hashed."""
    prefix = f"backend/app/factory/vendor_blocks_mirror/{block_id}"
    listed = _run(
        ["git", "ls-tree", "-r", "--name-only", LOCK_ERA_MIRROR_SHA, prefix],
        cwd=repo,
    )
    if listed.returncode != 0 or not listed.stdout.strip():
        raise SystemExit(
            f"sync_vendor_mirror: {block_id} is not in the Store and "
            f"lock-era tree {LOCK_ERA_MIRROR_SHA}:{prefix} is missing"
        )
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel in listed.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        blob = _run(["git", "show", f"{LOCK_ERA_MIRROR_SHA}:{rel}"], cwd=repo)
        if blob.returncode != 0:
            raise SystemExit(
                f"sync_vendor_mirror: cannot read {LOCK_ERA_MIRROR_SHA}:{rel}"
            )
        out = dest / Path(rel).relative_to(prefix)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = blob.stdout
        if isinstance(payload, str):
            out.write_bytes(payload.encode("utf-8"))
        else:
            out.write_bytes(payload or b"")


def sync_blocks(
    *,
    lock_path: Path,
    mirror_root: Path,
    blocks_root: Optional[Path] = None,
    only: Optional[Iterable[str]] = None,
    repo_root: Optional[Path] = None,
) -> list[dict]:
    lock = _load_lock(lock_path)
    wanted = set(only) if only is not None else None
    store_cache: dict[str, Path] = {}
    reports: list[dict] = []
    repo = Path(repo_root) if repo_root else REPO_ROOT

    for block_id in sorted(lock["blocks"]):
        if wanted is not None and block_id not in wanted:
            continue
        rec = lock["blocks"][block_id] or {}
        pinned = str(rec.get("content_hash") or "")
        sha = _pin_sha(lock, rec)
        if not sha:
            raise SystemExit(f"sync_vendor_mirror: no store SHA for {block_id}")
        if sha not in store_cache:
            store_cache[sha] = ensure_store(sha, blocks_root=blocks_root)
        store = store_cache[sha]
        src = store / "block_registry" / block_id
        dest = Path(mirror_root) / block_id
        origin = "store"
        siblings: list[str] = []
        if src.is_dir():
            _copy_tree(src, dest)
            siblings = _copy_siblings(store, block_id, Path(mirror_root))
        elif str(rec.get("source") or "") == "factory-vendor-mirror":
            origin = "lock-era-mirror"
            _restore_lock_era_mirror(block_id, dest, repo)
        else:
            raise SystemExit(
                f"sync_vendor_mirror: {block_id} missing from Store {sha} "
                f"block_registry/ and is not a factory-vendor-mirror pin"
            )
        actual = block_content_hash(dest)
        reports.append(
            {
                "id": block_id,
                "origin": origin,
                "sha": sha,
                "siblings": siblings,
                "match": actual == pinned,
                "pinned_hash": pinned,
                "mirror_hash": actual,
                "files": sum(
                    1
                    for p in dest.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                ),
            }
        )
        if actual != pinned:
            raise SystemExit(
                f"sync_vendor_mirror: {block_id} still mismatches after copy "
                f"lock={pinned} mirror={actual} origin={origin}"
            )
    return reports


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--lock", type=Path, default=None)
    parser.add_argument("--mirror", type=Path, default=None)
    parser.add_argument("--blocks-root", type=Path, default=None)
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated block ids (default: every lock entry)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    lock_path = Path(args.lock) if args.lock else default_lock_path()
    mirror_root = Path(args.mirror) if args.mirror else vendor_mirror_root()
    only = [p.strip() for p in str(args.only).split(",") if p.strip()] or None
    reports = sync_blocks(
        lock_path=lock_path,
        mirror_root=mirror_root,
        blocks_root=Path(args.blocks_root) if args.blocks_root else None,
        only=only,
    )
    for rec in reports:
        sib = ",".join(rec["siblings"]) if rec["siblings"] else "-"
        print(
            f"{rec['id']}: {rec['origin']} files={rec['files']} "
            f"siblings={sib} match={rec['match']}"
        )
    print(f"ok: re-vendored {len(reports)} lock-pinned block(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
