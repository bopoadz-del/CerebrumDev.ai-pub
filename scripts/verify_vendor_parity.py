#!/usr/bin/env python3
"""Fail-closed: vendor_blocks_mirror content_hash must match blocks.lock.json.

Uses the SAME hash as the lock builder (#401):
``app.factory.blocks_lock.block_content_hash``. Do not invent a second hash.

Usage:
  python3 scripts/verify_vendor_parity.py
  python3 scripts/verify_vendor_parity.py --lock PATH --mirror PATH [--blocks-root PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.factory.blocks_lock import (  # noqa: E402
    block_content_hash,
    default_lock_path,
)

COLUMNS = (
    "id",
    "pinned_hash",
    "mirror_hash",
    "match",
    "files_pinned",
    "files_mirror",
)


def hashed_file_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def _load_lock(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), dict):
        raise SystemExit(f"verify_vendor_parity: invalid lock at {path}")
    return data


def compare(
    *,
    lock_path: Path,
    mirror_root: Path,
    blocks_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Return (rows, all_match). Missing mirror dirs are B04 failures."""
    lock = _load_lock(Path(lock_path))
    rows: List[Dict[str, Any]] = []
    all_match = True
    for block_id in sorted(lock["blocks"]):
        rec = lock["blocks"][block_id] or {}
        pinned = str(rec.get("content_hash") or "")
        mirror_dir = Path(mirror_root) / block_id
        if mirror_dir.is_dir():
            mirror_hash = block_content_hash(mirror_dir)
            files_mirror = hashed_file_count(mirror_dir)
        else:
            mirror_hash = "MISSING"
            files_mirror = 0
        matched = bool(pinned) and mirror_hash == pinned
        store_dir = None
        if blocks_root is not None:
            candidate = Path(blocks_root) / "block_registry" / block_id
            if candidate.is_dir():
                store_dir = candidate
        if store_dir is not None:
            files_pinned: Any = hashed_file_count(store_dir)
        elif matched:
            files_pinned = files_mirror
        else:
            files_pinned = "?"
        rows.append(
            {
                "id": block_id,
                "pinned_hash": pinned or "MISSING",
                "mirror_hash": mirror_hash,
                "match": matched,
                "files_pinned": files_pinned,
                "files_mirror": files_mirror,
            }
        )
        if not matched:
            all_match = False
    return rows, all_match


def format_table(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return "(no lock entries)"
    widths = {col: len(col) for col in COLUMNS}
    rendered: List[Dict[str, str]] = []
    for row in rows:
        shown = {
            "id": str(row["id"]),
            "pinned_hash": str(row["pinned_hash"]),
            "mirror_hash": str(row["mirror_hash"]),
            "match": "yes" if row["match"] else "NO",
            "files_pinned": str(row["files_pinned"]),
            "files_mirror": str(row["files_mirror"]),
        }
        rendered.append(shown)
        for col in COLUMNS:
            widths[col] = max(widths[col], len(shown[col]))
    header = " | ".join(col.ljust(widths[col]) for col in COLUMNS)
    rule = "-+-".join("-" * widths[col] for col in COLUMNS)
    lines = [header, rule]
    for shown in rendered:
        lines.append(" | ".join(shown[col].ljust(widths[col]) for col in COLUMNS))
    matched = sum(1 for row in rows if row["match"])
    lines.append(f"{matched}/{len(rows)} match")
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--lock", type=Path, default=None, help="blocks.lock.json path")
    parser.add_argument("--mirror", type=Path, default=None, help="vendor_blocks_mirror path")
    parser.add_argument(
        "--blocks-root",
        type=Path,
        default=None,
        help="Optional Store checkout for files_pinned counts",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    lock_path = Path(args.lock) if args.lock else default_lock_path()
    # The Factory holds no blocks, so there is no local mirror to compare
    # against. What the lock must match is the STORE at the pinned commit --
    # the tree production's CLONER actually copies from. Fail closed when no
    # Store checkout is available: an unverifiable lock is not a verified one.
    if args.mirror:
        mirror_root = Path(args.mirror)
    else:
        store = args.blocks_root or os.environ.get("CEREBRUM_BLOCKS_ROOT") or ""
        if not store or not (Path(store) / "block_registry").is_dir():
            print(
                "verify_vendor_parity: FAIL no Store checkout to verify the lock "
                "against (set CEREBRUM_BLOCKS_ROOT or pass --blocks-root)",
                file=sys.stderr,
            )
            return 1
        mirror_root = Path(store) / "block_registry"
        if args.blocks_root is None:
            args.blocks_root = Path(store)
    rows, ok = compare(
        lock_path=lock_path,
        mirror_root=mirror_root,
        blocks_root=Path(args.blocks_root) if args.blocks_root else None,
    )
    print(format_table(rows))
    if not ok:
        bad = [row["id"] for row in rows if not row["match"]]
        print(
            "verify_vendor_parity: FAIL lock does not match the Store: " + ", ".join(bad),
            file=sys.stderr,
        )
        return 1
    print("ok: blocks.lock.json matches the pinned Store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
