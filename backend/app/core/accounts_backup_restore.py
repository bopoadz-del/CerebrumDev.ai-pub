"""Restore accounts from on-disk backup archives into ACCOUNTS_DATABASE_URL.

Nightly snapshots live under ``STORAGE_PATH/backups`` as
``cerebrumdev-backup-*.tar.gz``. A Postgres-backed run stores
``accounts.json`` (structured snapshot, accounts_snapshot.v1) and, when the
client matches the server, ``accounts.dump`` (pg_dump custom). Older archives
carry ``accounts.sql`` (SQL-INSERT dump, read by a SQL engine) and SQLite-era
archives ``accounts.db``.

This module lists those archives and merges dump rows into live Postgres
without wiping smoke accounts. ``prefer_source`` parks a conflicting live
email so a historical owner id (``acct_c38ae401…``) can reclaim it.

``cerebrum-builds`` is not the accounts store.
"""

from __future__ import annotations

import re
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core import backup as bk
from scripts.migrate_accounts_to_postgres import (
    HARD_VERIFY_TABLES,
    TABLES,
    AccountsRestoreError,
    _RESTORE_LOCK,
    _assert_no_secrets,
    list_sqlite_account_identities,
    migrate,
    require_accounts_database_url,
    run_restore,
    target_engine,
    verify_source_present,
)

_ARCHIVE_RE = re.compile(r"^cerebrumdev-backup-\d{8}T\d{6}Z\.tar\.gz$")
#: Restore preference: the structured snapshot every new archive carries,
#: then a SQLite-era database, then a legacy SQL-INSERT dump, then a custom
#: pg_dump (operator tool only -- see load_accounts_data).
_DUMP_MEMBERS = (bk.ACCOUNTS_SNAPSHOT_NAME, "accounts.db", "accounts.sql", "accounts.dump")


def _member_basename(name: str) -> str:
    return Path(name).name


def resolve_backup_archive(name: Optional[str]) -> Path:
    """Resolve a backup basename under ``backup_root()``. Rejects traversal."""
    root = bk.backup_root().resolve()
    if not name or name in {"latest", "auto"}:
        listing = list_backup_archives()
        recommended = listing.get("recommended")
        if not recommended:
            raise AccountsRestoreError(
                "archive_missing",
                f"no backup archive with an accounts dump under {root}",
                extra={"root": str(root)},
            )
        name = recommended
    base = Path(str(name)).name
    if not _ARCHIVE_RE.match(base):
        raise AccountsRestoreError(
            "archive_unsafe",
            "archive must be a cerebrumdev-backup-YYYYMMDDThhmmssZ.tar.gz basename",
            extra={"archive": base},
        )
    path = (root / base).resolve()
    if path.parent != root or not path.is_file():
        raise AccountsRestoreError(
            "archive_missing",
            f"archive not found under backup root: {base}",
            extra={"archive": base, "root": str(root)},
        )
    return path


def _safe_tar_members(archive: Path) -> List[str]:
    with tarfile.open(archive, "r:gz") as tar:
        names: List[str] = []
        for member in tar.getmembers():
            raw = member.name
            if raw.startswith("/") or ".." in Path(raw).parts:
                raise AccountsRestoreError(
                    "archive_unsafe",
                    f"refusing unsafe archive member: {raw}",
                    extra={"archive": archive.name},
                )
            names.append(raw)
        return names


def _extract_member(archive: Path, dest: Path, wanted: Iterable[str]) -> Dict[str, Path]:
    wanted_set = {Path(w).name for w in wanted}
    found: Dict[str, Path] = {}
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise AccountsRestoreError(
                    "archive_unsafe",
                    f"refusing unsafe archive member: {member.name}",
                )
            base = _member_basename(member.name)
            if base not in wanted_set or not member.isfile():
                continue
            extracted = dest / base
            src = tar.extractfile(member)
            if src is None:
                continue
            extracted.write_bytes(src.read())
            found[base] = extracted
    return found


def read_structured_snapshot(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Rows from an ``accounts_snapshot.v1`` JSON document (see backup.py)."""
    import json

    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != bk.ACCOUNTS_SNAPSHOT_SCHEMA:
        raise AccountsRestoreError(
            "dump_unreadable",
            f"{path.name} is not an {bk.ACCOUNTS_SNAPSHOT_SCHEMA} document",
        )
    out: Dict[str, List[Dict[str, Any]]] = {}
    for table in TABLES:
        block = (doc.get("tables") or {}).get(table)
        if not block:
            continue
        columns = list(block.get("columns") or [])
        out[table] = [dict(zip(columns, row)) for row in block.get("rows") or []]
    return out


def read_legacy_sql_dump(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Rows from a legacy SQL-INSERT accounts dump, read by a SQL ENGINE.

    The statements run in an in-memory SQLite whose tables come from the
    accounts store's own schema (accounts_store._META), and the rows are read
    back through that schema -- no SQL text is parsed here. Statement
    boundaries come from sqlite3.complete_statement; a statement for a table
    outside the schema fails with sqlite3.OperationalError and is skipped
    (it is not an accounts table). Boolean columns come back as 0/1 from
    SQLite and are restored to bool from the schema's column types.
    """
    import sqlalchemy as sa

    from app.core.accounts_store import _META

    conn = sqlite3.connect(":memory:")
    try:
        engine = sa.create_engine("sqlite://", creator=lambda: conn)
        _META.create_all(engine)
        statement = ""
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                statement += line
                if not sqlite3.complete_statement(statement):
                    continue
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass
                statement = ""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for table_name in TABLES:
            table = _META.tables.get(table_name)
            if table is None:
                continue
            booleans = {c.name for c in table.columns if isinstance(c.type, sa.Boolean)}
            cursor = conn.execute(sa.select(table).compile(dialect=engine.dialect).string)
            columns = [d[0] for d in cursor.description]
            rows = []
            for raw in cursor.fetchall():
                row = dict(zip(columns, raw))
                for name in booleans:
                    if row.get(name) is not None:
                        row[name] = bool(row[name])
                rows.append(row)
            if rows:
                out[table_name] = rows
        return out
    finally:
        conn.close()


def _artifact_kind(path: Path) -> str:
    """What an archive member is: by its magic bytes, else by its member name."""
    head = path.read_bytes()[:16]
    if head.startswith(b"SQLite format 3"):
        return "sqlite"
    if head.startswith(b"PGDMP"):
        return "pg_dump"
    return {
        bk.ACCOUNTS_SNAPSHOT_NAME: "structured",
        "accounts.sql": "sql",
        "accounts.db": "sqlite",
        "accounts.dump": "pg_dump",
    }.get(path.name, "unknown")


def load_accounts_data(path: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """Return (table->rows, artifact name)."""
    kind = _artifact_kind(path)
    if kind == "structured":
        data = read_structured_snapshot(path)
    elif kind == "sqlite":
        data = read_sqlite_tables(path)
    elif kind == "sql":
        data = read_legacy_sql_dump(path)
    elif kind == "pg_dump":
        # A custom pg_dump is decoded by pg_restore into COPY text, which this
        # service would have to parse. It does not: every archive written
        # since accounts_snapshot.v1 also carries accounts.json, and an older
        # pg_dump-only archive is restored with the operator tool
        # (pg_restore --data-only --dbname <accounts database url> <file>).
        raise AccountsRestoreError(
            "dump_requires_operator_tool",
            f"{path.name} is a custom pg_dump: restore it with "
            "pg_restore --data-only --dbname <accounts database url>; "
            "archives that carry accounts.json restore through this endpoint",
            extra={"dump": path.name},
        )
    else:
        raise AccountsRestoreError(
            "dump_unreadable",
            f"unrecognized accounts artifact: {path.name}",
        )
    if not data.get("accounts") and kind != "sqlite":
        raise AccountsRestoreError("dump_unreadable", f"{path.name} holds no accounts rows")
    return data, path.name


def read_sqlite_tables(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in TABLES:
            if table not in present:
                continue
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            out[table] = [dict(r) for r in rows]
    finally:
        conn.close()
    return out


def _identities_from_data(data: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    rows = []
    for row in data.get("accounts") or []:
        account_id = str(row.get("id") or "")
        email = str(row.get("email") or "")
        if account_id or email:
            rows.append({"id": account_id, "email": email})
    rows.sort(key=lambda r: (r["email"], r["id"]))
    return rows


def _peek_archive_accounts(archive: Path, members: List[str]) -> List[Dict[str, str]]:
    bases = {_member_basename(n) for n in members}
    for candidate in _DUMP_MEMBERS:
        if candidate not in bases:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            extracted = _extract_member(archive, Path(tmp), [candidate])
            path = extracted.get(candidate)
            if path is None:
                continue
            try:
                if _artifact_kind(path) == "sqlite":
                    return list_sqlite_account_identities(path)
                data, _ = load_accounts_data(path)
                return _identities_from_data(data)
            except Exception:  # noqa: BLE001 — listing must not 500
                return []
    return []


def list_backup_archives() -> Dict[str, Any]:
    root = bk.backup_root()
    archives: List[Dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.glob("cerebrumdev-backup-*.tar.gz"), reverse=True):
            if not path.is_file() or not _ARCHIVE_RE.match(path.name):
                continue
            try:
                members = _safe_tar_members(path)
            except AccountsRestoreError:
                continue
            bases = {_member_basename(n) for n in members}
            item = {
                "name": path.name,
                "bytes": path.stat().st_size,
                "accounts_json": bk.ACCOUNTS_SNAPSHOT_NAME in bases,
                "accounts_dump": "accounts.dump" in bases,
                "accounts_sql": "accounts.sql" in bases,
                "accounts_db": "accounts.db" in bases,
                "members": sorted(bases),
                "source_accounts": _peek_archive_accounts(path, members),
            }
            item["has_accounts_artifact"] = bool(
                item["accounts_json"] or item["accounts_dump"]
                or item["accounts_sql"] or item["accounts_db"]
            )
            archives.append(item)

    recommended = None
    for key in ("accounts_json", "accounts_db", "accounts_sql", "accounts_dump"):
        for item in archives:
            if item.get(key):
                recommended = item["name"]
                break
        if recommended:
            break

    payload = {
        "root": str(root),
        "count": len(archives),
        "recommended": recommended,
        "archives": archives,
        "note": (
            "On-disk backup archives only. cerebrum-builds is not the accounts "
            "store. POST /v1/ops/accounts-restore/from-backup?archive=<name> "
            "merges dump rows into ACCOUNTS_DATABASE_URL."
        ),
    }
    _assert_no_secrets(payload)
    return payload


def run_restore_from_backup(
    *,
    archive: Optional[str] = None,
    force: bool = True,
    dry_run: bool = False,
    prefer_source: bool = True,
    verify: bool = True,
) -> Dict[str, Any]:
    """Merge accounts from a backup archive into ACCOUNTS_DATABASE_URL."""
    with _RESTORE_LOCK:
        require_accounts_database_url()
        path = resolve_backup_archive(archive)
        with tempfile.TemporaryDirectory() as tmp:
            extracted = _extract_member(path, Path(tmp), _DUMP_MEMBERS)
            artifact = None
            for name in _DUMP_MEMBERS:
                if name in extracted:
                    artifact = extracted[name]
                    break
            if artifact is None:
                raise AccountsRestoreError(
                    "dump_missing",
                    f"{path.name} has none of: {', '.join(_DUMP_MEMBERS)}",
                    extra={"archive": path.name},
                )
            data, dump_kind = load_accounts_data(artifact)
            engine = target_engine()
            try:
                report = migrate(
                    engine,
                    data,
                    force=force,
                    dry_run=dry_run,
                    prefer_source=prefer_source,
                )
                report.source = "backup"
                report.archive = path.name
                report.dump_kind = dump_kind
                report.sqlite_path = str(artifact)
                report.sqlite_present = dump_kind == "accounts.db"
                if verify and not dry_run:
                    missing = verify_source_present(
                        engine, data, report.email_conflicts
                    )
                    hard = {
                        table: keys
                        for table, keys in missing.items()
                        if table in HARD_VERIFY_TABLES
                    }
                    advisory = {
                        table: keys
                        for table, keys in missing.items()
                        if table not in HARD_VERIFY_TABLES
                    }
                    report.missing_advisory = {
                        table: [list(k) for k in keys]
                        for table, keys in advisory.items()
                    }
                    report.verified = not hard
                    if hard:
                        report.ok = False
                        raise AccountsRestoreError(
                            "verify_failed",
                            "source account primary keys missing after backup merge",
                            extra={
                                **report.as_public_dict(),
                                "missing_keys": {
                                    table: [list(k) for k in keys]
                                    for table, keys in hard.items()
                                },
                            },
                        )
                payload = report.as_public_dict()
                payload["source_accounts"] = _identities_from_data(data)
                _assert_no_secrets(payload)
                return payload
            finally:
                engine.dispose()


# Keep the live-disk SQLite entry importable from one place.
run_restore_from_sqlite = run_restore
