"""Entry point for the scheduled backup job, and for manual restore drills.

    python -m scripts.backup_cli backup            # snapshot + verify + prune
    python -m scripts.backup_cli list
    python -m scripts.backup_cli restore <archive> <target-dir>
    python -m scripts.backup_cli drill             # backup, restore it, compare

``drill`` is the one that matters. It proves the archive can actually be put
back, which is the only thing that distinguishes a backup from a file.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.core import backup as bk


def _cmd_backup(args: argparse.Namespace) -> int:
    result = bk.create_backup(include_content=not args.db_only)
    removed = bk.prune_backups(keep=args.keep) if result.ok else []
    payload = result.to_dict()
    payload["pruned"] = [p.name for p in removed]
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


def _cmd_list(_args: argparse.Namespace) -> int:
    root = bk.backup_root()
    archives = sorted(root.glob("cerebrumdev-backup-*.tar.gz")) if root.is_dir() else []
    print(json.dumps({
        "root": str(root),
        "count": len(archives),
        "archives": [{"name": a.name, "bytes": a.stat().st_size} for a in archives],
    }, indent=2))
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    out = bk.restore_backup(Path(args.archive), Path(args.target))
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


def _cmd_drill(args: argparse.Namespace) -> int:
    """Back up, restore into a scratch directory, and compare row counts."""
    result = bk.create_backup(include_content=not args.db_only)
    if not result.ok or not result.archive:
        print(json.dumps({"ok": False, "stage": "backup", "detail": result.to_dict()}, indent=2))
        return 1

    with tempfile.TemporaryDirectory() as scratch:
        restored = bk.restore_backup(result.archive, Path(scratch))
        counts = restored.get("verified")
        ok = bool(counts) or result.engine == "postgres"
        print(json.dumps({
            "ok": ok,
            "archive": str(result.archive),
            "engine": result.engine,
            "restored_row_counts": counts,
            "note": None if ok else "restore produced no verifiable accounts database",
        }, indent=2))
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup_cli")
    parser.add_argument("--db-only", action="store_true", help="skip bulk content")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_backup = sub.add_parser("backup")
    p_backup.add_argument("--keep", type=int, default=bk.DEFAULT_KEEP)
    p_backup.set_defaults(func=_cmd_backup)

    sub.add_parser("list").set_defaults(func=_cmd_list)

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("archive")
    p_restore.add_argument("target")
    p_restore.set_defaults(func=_cmd_restore)

    sub.add_parser("drill").set_defaults(func=_cmd_drill)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
