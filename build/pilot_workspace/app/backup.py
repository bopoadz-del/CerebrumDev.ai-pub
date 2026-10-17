"""Backup, restore, and retention for platform.db.

Uses SQLite's online backup API (not a file copy) so a live WAL
writer cannot produce a torn snapshot. A restore that has not been
drilled is not a restore — tests/test_data_lifecycle.py performs
backup → wipe → restore → assert rows.

Same-disk BACKUP_DIR (the default) protects against logical loss,
not disk loss. The mounted Render disk is a SPOF.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.store import db_path

DEFAULT_KEEP = 14


def _utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_root() -> Path:
    override = os.getenv("BACKUP_DIR", "").strip()
    if override:
        return Path(override)
    return Path(os.getenv("STORAGE_PATH", "./data")) / "backups"


def _sidecar_paths(db: Path) -> List[Path]:
    return [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]


def create_backup() -> Path:
    src = db_path()
    dest_dir = backup_root()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"platform-{_utcstamp()}.db"
    src_conn = sqlite3.connect(str(src))
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    prune_backups(keep=DEFAULT_KEEP)
    return dest


def restore_backup(archive: Path, dest: Path | None = None) -> Path:
    dest = dest or db_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    for side in _sidecar_paths(dest):
        if side.exists():
            side.unlink()
    src_conn = sqlite3.connect(str(archive))
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    return dest


def wipe_database() -> None:
    target = db_path()
    if target.exists():
        target.unlink()
    for side in _sidecar_paths(target):
        if side.exists():
            side.unlink()


def prune_backups(keep: int = DEFAULT_KEEP) -> List[Path]:
    root = backup_root()
    if not root.is_dir():
        return []
    archives = sorted(root.glob("platform-*.db"))
    removed: List[Path] = []
    for stale in archives[: max(0, len(archives) - keep)]:
        stale.unlink()
        removed.append(stale)
    return removed


def list_backups() -> List[Path]:
    root = backup_root()
    if not root.is_dir():
        return []
    return sorted(root.glob("platform-*.db"))
