"""One-time cutover of the accounts database from SQLite to Postgres.

    python -m scripts.migrate_accounts_to_postgres --dry-run
    python -m scripts.migrate_accounts_to_postgres --verify
    python -m scripts.migrate_accounts_to_postgres --force --verify

Why this exists: setting ``ACCOUNTS_DATABASE_URL`` switches engines but copies
nothing. Pointing the live service at an empty Postgres loses every account,
and because the app boots fine and simply reports no users, nobody notices
until a customer cannot log in.

Default insert is row-for-row inside one transaction and refuses a non-empty
target. ``--force`` is merge mode: insert missing ids/emails only, never wipe
or overwrite existing Postgres rows (smoke accounts stay). ``--verify``
confirms every non-conflict source **account** primary key landed. Missing
``session_owners`` / token rows are advisory — those may live outside the
dump. Counts may differ after a merge.

``cerebrum-builds`` is not the accounts store. This script reads disk SQLite
at ``STORAGE_PATH/accounts.db`` and writes only to ``ACCOUNTS_DATABASE_URL``.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import sqlalchemy as sa

TABLES = ["accounts", "api_keys", "login_tokens", "session_owners", "usage_counters"]

PK_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "accounts": ("id",),
    "api_keys": ("id",),
    "login_tokens": ("id",),
    "session_owners": ("session_id",),
    "usage_counters": ("account_id", "counter", "period"),
}

UNIQUE_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "accounts": ("email",),
    "api_keys": ("key_hash",),
    "login_tokens": ("token_hash",),
}

ACCOUNT_FK_TABLES = {
    "api_keys",
    "login_tokens",
    "session_owners",
    "usage_counters",
}

# Accounts must land. session_owners / tokens may live outside the dump.
HARD_VERIFY_TABLES = frozenset({"accounts"})

# Never leak credential material in HTTP / CLI summaries.
SECRET_COLUMNS = frozenset(
    {
        "password_hash",
        "verify_token_hash",
        "reset_token_hash",
        "key_hash",
        "token_hash",
    }
)

_RESTORE_LOCK = threading.RLock()


class AccountsRestoreError(Exception):
    """Structured failure for the CLI and the master-key HTTP wrapper."""

    def __init__(
        self,
        code: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


@dataclass
class EmailConflict:
    sqlite_id: str
    sqlite_email: str
    postgres_id: str


@dataclass
class RestoreReport:
    ok: bool
    mode: str
    dry_run: bool
    force: bool
    sqlite_path: str
    sqlite_present: bool
    sqlite_counts: Dict[str, int]
    postgres_counts_before: Dict[str, int]
    postgres_counts_after: Dict[str, int]
    emails_migrated: List[str]
    emails_skipped_existing: List[str]
    email_conflicts: List[Dict[str, str]]
    inserted: Dict[str, int]
    skipped: Dict[str, int]
    verified: Optional[bool] = None
    emails_displaced: List[Dict[str, str]] = field(default_factory=list)
    missing_advisory: Dict[str, List[List[Any]]] = field(default_factory=dict)
    source: str = "sqlite"
    archive: Optional[str] = None
    dump_kind: Optional[str] = None
    prefer_source: bool = False
    note: str = (
        "Merge inserts missing account ids/emails only. Existing Postgres rows "
        "are never wiped. cerebrum-builds is not the accounts store."
    )

    def as_public_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        _assert_no_secrets(payload)
        return payload


def sqlite_path() -> Path:
    override = os.getenv("ACCOUNTS_DB_PATH", "").strip()
    if override:
        return Path(override)
    return Path(os.getenv("STORAGE_PATH", "./storage")) / "accounts.db"


def accounts_database_url() -> str:
    return os.getenv("ACCOUNTS_DATABASE_URL", "").strip()


def require_accounts_database_url() -> str:
    url = accounts_database_url()
    if not url:
        raise AccountsRestoreError(
            "accounts_url_unset",
            "ACCOUNTS_DATABASE_URL is not set; refusing to migrate. "
            "This must stay set on the web service — unsetting it falls back "
            "to disk SQLite and hides the real accounts. "
            "cerebrum-builds is not the accounts store.",
        )
    return url


def normalise_url(raw: str) -> str:
    from app.core.accounts_store import (
        normalize_accounts_database_url,
        prepare_libpq_client_env,
    )

    prepare_libpq_client_env()
    return normalize_accounts_database_url(raw)


def target_engine(url: Optional[str] = None) -> sa.engine.Engine:
    resolved = url if url is not None else require_accounts_database_url()
    return sa.create_engine(normalise_url(resolved), pool_pre_ping=True)


def _norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _row_key(row: Dict[str, Any], columns: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(row.get(col) for col in columns)


def _assert_no_secrets(payload: Any) -> None:
    if isinstance(payload, dict):
        leaked = SECRET_COLUMNS.intersection(payload)
        if leaked:
            raise RuntimeError(f"refusing to emit secret columns: {sorted(leaked)}")
        for value in payload.values():
            _assert_no_secrets(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secrets(item)


def read_sqlite(path: Path) -> Dict[str, List[Dict[str, Any]]]:
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


def list_sqlite_account_identities(path: Optional[Path] = None) -> List[Dict[str, str]]:
    src = path or sqlite_path()
    if not src.is_file():
        return []
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "accounts" not in present:
            return []
        rows = conn.execute(
            'SELECT id, email FROM "accounts" ORDER BY email, id'
        ).fetchall()
        return [{"id": str(r["id"]), "email": str(r["email"])} for r in rows]
    finally:
        conn.close()


def list_postgres_account_identities(
    engine: Optional[sa.engine.Engine] = None,
) -> List[Dict[str, str]]:
    own_engine = engine is None
    eng = engine or target_engine()
    try:
        insp = sa.inspect(eng)
        if not insp.has_table("accounts"):
            return []
        with eng.connect() as conn:
            rows = conn.execute(
                sa.text('SELECT id, email FROM "accounts" ORDER BY email, id')
            ).fetchall()
        return [{"id": str(r[0]), "email": str(r[1])} for r in rows]
    finally:
        if own_engine:
            eng.dispose()


def target_counts(engine: sa.engine.Engine) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    insp = sa.inspect(engine)
    with engine.connect() as conn:
        for table in TABLES:
            if not insp.has_table(table):
                continue
            counts[table] = conn.execute(
                sa.text(f'SELECT COUNT(*) FROM "{table}"')
            ).scalar_one()
    return counts


def _existing_keys(
    conn: sa.engine.Connection,
    table: sa.Table,
    columns: Sequence[str],
) -> set:
    if not columns or any(col not in table.c for col in columns):
        return set()
    rows = conn.execute(sa.select(*[table.c[col] for col in columns])).fetchall()
    if len(columns) == 1 and columns[0] == "email":
        return {_norm_email(r[0]) for r in rows}
    return {tuple(r) for r in rows}


def _existing_account_ids(conn: sa.engine.Connection, table: sa.Table) -> set:
    if "id" not in table.c:
        return set()
    return {r[0] for r in conn.execute(sa.select(table.c.id)).fetchall()}


def _existing_email_owners(conn: sa.engine.Connection, table: sa.Table) -> Dict[str, str]:
    if "id" not in table.c or "email" not in table.c:
        return {}
    return {
        _norm_email(email): str(account_id)
        for account_id, email in conn.execute(
            sa.select(table.c.id, table.c.email)
        ).fetchall()
    }


def displaced_email_for(postgres_id: str) -> str:
    """Park a conflicting live email so the backup id can reclaim it."""
    safe = re.sub(r"[^a-zA-Z0-9_]+", "", postgres_id) or "unknown"
    return f"displaced+{safe}@invalid.cerebrum-dev.restore"


def _displace_conflicting_emails(
    conn: sa.engine.Connection,
    tbl: sa.Table,
    rows: List[Dict[str, Any]],
    *,
    dry_run: bool,
) -> List[Dict[str, str]]:
    """Free emails that a backup account needs, without deleting live rows."""
    owners = _existing_email_owners(conn, tbl)
    existing_emails = set(owners)
    displaced: List[Dict[str, str]] = []
    for row in rows:
        source_id = str(row.get("id") or "")
        email = _norm_email(row.get("email"))
        if not source_id or not email:
            continue
        owner = owners.get(email)
        if not owner or owner == source_id:
            continue
        parked = displaced_email_for(owner)
        n = 0
        while parked in existing_emails:
            n += 1
            parked = displaced_email_for(f"{owner}_{n}")
        if not dry_run:
            conn.execute(
                tbl.update().where(tbl.c.id == owner).values(email=parked)
            )
        owners.pop(email, None)
        owners[parked] = owner
        existing_emails.add(parked)
        displaced.append(
            {
                "postgres_id": owner,
                "original_email": email,
                "displaced_email": parked,
                "source_id": source_id,
            }
        )
    return displaced


def _plan_table(
    table_name: str,
    rows: List[Dict[str, Any]],
    tbl: sa.Table,
    conn: sa.engine.Connection,
    known_account_ids: set,
) -> Tuple[List[Dict[str, Any]], int, List[EmailConflict], List[str], List[str]]:
    """Return (payload, skipped, conflicts, migrated_emails, skipped_emails)."""
    pk = PK_COLUMNS[table_name]
    unique = UNIQUE_COLUMNS.get(table_name, ())
    existing_pk = _existing_keys(conn, tbl, pk)
    existing_unique = _existing_keys(conn, tbl, unique) if unique else set()
    email_owners = (
        _existing_email_owners(conn, tbl) if table_name == "accounts" else {}
    )
    cols = {c.name for c in tbl.columns}

    payload: List[Dict[str, Any]] = []
    skipped = 0
    conflicts: List[EmailConflict] = []
    migrated_emails: List[str] = []
    skipped_emails: List[str] = []

    for row in rows:
        key = _row_key(row, pk)
        if key in existing_pk:
            skipped += 1
            if table_name == "accounts":
                skipped_emails.append(str(row.get("email") or ""))
            continue

        if table_name == "accounts":
            email = _norm_email(row.get("email"))
            owner = email_owners.get(email)
            if owner and owner != str(row.get("id")):
                conflicts.append(
                    EmailConflict(
                        sqlite_id=str(row.get("id") or ""),
                        sqlite_email=str(row.get("email") or ""),
                        postgres_id=owner,
                    )
                )
                skipped += 1
                continue

        if table_name in ACCOUNT_FK_TABLES:
            account_id = row.get("account_id")
            if account_id and account_id not in known_account_ids:
                skipped += 1
                continue

        if unique:
            uniq_key = (
                _norm_email(row.get("email"))
                if table_name == "accounts"
                else _row_key(row, unique)
            )
            if uniq_key in existing_unique:
                skipped += 1
                continue

        cleaned = {k: v for k, v in row.items() if k in cols}
        payload.append(cleaned)
        existing_pk.add(key)
        if unique:
            if table_name == "accounts":
                email_norm = _norm_email(row.get("email"))
                existing_unique.add(email_norm)
                email_owners[email_norm] = str(row.get("id"))
            else:
                existing_unique.add(_row_key(row, unique))
        if table_name == "accounts":
            migrated_emails.append(str(row.get("email") or ""))
            known_account_ids.add(row.get("id"))

    return payload, skipped, conflicts, migrated_emails, skipped_emails


def compare_stores() -> Dict[str, Any]:
    """Ids + emails only. Used by GET /v1/ops/accounts-restore."""
    src = sqlite_path()
    sqlite_accounts = list_sqlite_account_identities(src)
    configured = bool(accounts_database_url())
    postgres_accounts: List[Dict[str, str]] = []
    postgres_error: Optional[str] = None
    if configured:
        engine = target_engine()
        try:
            postgres_accounts = list_postgres_account_identities(engine)
        except Exception as exc:  # noqa: BLE001 — diagnostic listing
            postgres_error = type(exc).__name__
        finally:
            engine.dispose()
    payload = {
        "sqlite_path": str(src),
        "sqlite_present": src.is_file(),
        "sqlite_accounts": sqlite_accounts,
        "sqlite_count": len(sqlite_accounts),
        "postgres_configured": configured,
        "postgres_accounts": postgres_accounts,
        "postgres_count": len(postgres_accounts),
        "note": (
            "Ids and emails only. cerebrum-builds is not the accounts store; "
            "this compares disk SQLite (STORAGE_PATH/accounts.db) with "
            "ACCOUNTS_DATABASE_URL. ACCOUNTS_DATABASE_URL must stay set."
        ),
    }
    if postgres_error:
        payload["postgres_error"] = postgres_error
    _assert_no_secrets(payload)
    return payload


def migrate(
    engine: sa.engine.Engine,
    data: Dict[str, List[Dict[str, Any]]],
    force: bool,
    *,
    dry_run: bool = False,
    prefer_source: bool = False,
) -> RestoreReport:
    existing = target_counts(engine)
    non_empty = {t: n for t, n in existing.items() if n}
    if non_empty and not force:
        raise AccountsRestoreError(
            "target_not_empty",
            "target already holds rows; refusing to merge. "
            "Re-run with force=true / --force to insert missing ids/emails "
            "only (existing Postgres rows are not wiped).",
            extra={"postgres_counts": existing, "non_empty": non_empty},
        )

    md = sa.MetaData()
    present = [t for t in TABLES if t in data]
    if present:
        md.reflect(bind=engine, only=present)

    sqlite_counts = {t: len(rows) for t, rows in data.items()}
    inserted: Dict[str, int] = {t: 0 for t in TABLES}
    skipped: Dict[str, int] = {t: 0 for t in TABLES}
    emails_migrated: List[str] = []
    emails_skipped: List[str] = []
    conflicts: List[EmailConflict] = []
    displaced: List[Dict[str, str]] = []
    mode = "merge" if force else "insert"

    def _apply(conn: sa.engine.Connection) -> None:
        known_ids: set = set()
        accounts_tbl = md.tables.get("accounts")
        if accounts_tbl is not None:
            known_ids = _existing_account_ids(conn, accounts_tbl)
            if prefer_source:
                displaced.extend(
                    _displace_conflicting_emails(
                        conn,
                        accounts_tbl,
                        data.get("accounts") or [],
                        dry_run=dry_run,
                    )
                )

        for table in TABLES:
            rows = data.get(table) or []
            if not rows:
                continue
            tbl = md.tables.get(table)
            if tbl is None:
                raise AccountsRestoreError(
                    "schema_missing",
                    f"table {table} does not exist in the target; run alembic first",
                )
            payload, n_skip, table_conflicts, migrated, skipped_emails = _plan_table(
                table, rows, tbl, conn, known_ids
            )
            skipped[table] = n_skip
            conflicts.extend(table_conflicts)
            emails_migrated.extend(migrated)
            emails_skipped.extend(skipped_emails)
            if payload and not dry_run:
                conn.execute(tbl.insert(), payload)
            inserted[table] = len(payload)

    if dry_run:
        with engine.connect() as conn:
            _apply(conn)
        after = existing
        mode = "dry_run"
    else:
        with engine.begin() as conn:
            _apply(conn)
        after = target_counts(engine)

    return RestoreReport(
        ok=True,
        mode=mode,
        dry_run=dry_run,
        force=force,
        sqlite_path="",
        sqlite_present=True,
        sqlite_counts=sqlite_counts,
        postgres_counts_before=existing,
        postgres_counts_after=after,
        emails_migrated=sorted(e for e in emails_migrated if e),
        emails_skipped_existing=sorted(e for e in emails_skipped if e),
        email_conflicts=[asdict(c) for c in conflicts],
        inserted=inserted,
        skipped=skipped,
        emails_displaced=displaced,
        prefer_source=prefer_source,
    )


def verify_source_present(
    engine: sa.engine.Engine,
    data: Dict[str, List[Dict[str, Any]]],
    conflicts: Iterable[Dict[str, str]],
) -> Dict[str, List[Tuple[Any, ...]]]:
    """Source PKs that are still missing after excluding email conflicts."""
    conflict_ids = {c.get("sqlite_id") for c in conflicts}
    missing: Dict[str, List[Tuple[Any, ...]]] = {}
    md = sa.MetaData()
    present = [t for t in TABLES if t in data]
    if present:
        md.reflect(bind=engine, only=present)
    with engine.connect() as conn:
        for table, rows in data.items():
            tbl = md.tables.get(table)
            if tbl is None:
                continue
            pk = PK_COLUMNS[table]
            existing = _existing_keys(conn, tbl, pk)
            absent: List[Tuple[Any, ...]] = []
            for row in rows:
                if table == "accounts" and str(row.get("id")) in conflict_ids:
                    continue
                if table in ACCOUNT_FK_TABLES and row.get("account_id") in conflict_ids:
                    continue
                key = _row_key(row, pk)
                if key not in existing:
                    absent.append(key)
            if absent:
                missing[table] = absent
    return missing


def run_restore(
    *,
    force: bool = False,
    dry_run: bool = False,
    verify: bool = True,
) -> RestoreReport:
    """Library entry used by the CLI and the master-key admin endpoint."""
    with _RESTORE_LOCK:
        src = sqlite_path()
        url = require_accounts_database_url()
        if not src.is_file():
            raise AccountsRestoreError(
                "sqlite_missing",
                f"no SQLite accounts database at {src}",
                extra={"sqlite_path": str(src)},
            )

        data = read_sqlite(src)
        engine = target_engine(url)
        try:
            report = migrate(engine, data, force=force, dry_run=dry_run)
            report.sqlite_path = str(src)
            report.sqlite_present = True
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
                        "source account primary keys missing from target after migrate",
                        extra={
                            **report.as_public_dict(),
                            "missing_keys": {
                                table: [list(k) for k in keys]
                                for table, keys in hard.items()
                            },
                        },
                    )
            return report
        finally:
            engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="migrate_accounts_to_postgres")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="confirm every non-conflict source primary key landed in the target",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help=(
            "merge into a non-empty target: insert missing ids/emails only; "
            "never wipe existing Postgres rows"
        ),
    )
    args = ap.parse_args(argv)

    src = sqlite_path()
    if not src.is_file():
        print(f"no SQLite accounts database at {src}; nothing to migrate")
        return 0

    try:
        require_accounts_database_url()
    except AccountsRestoreError as exc:
        print(exc.message)
        return 2

    data = read_sqlite(src)
    summary = {t: len(rows) for t, rows in data.items()}
    print(f"source {src}: {summary}")

    if args.dry_run:
        try:
            report = run_restore(force=args.force, dry_run=True, verify=False)
        except AccountsRestoreError as exc:
            print(exc.message)
            if exc.extra:
                print(exc.extra)
            return 2 if exc.code == "target_not_empty" else 1
        print("dry run; nothing written")
        print(
            f"would insert: {report.inserted} skip: {report.skipped} "
            f"emails: {report.emails_migrated}"
        )
        return 0

    try:
        report = run_restore(force=args.force, dry_run=False, verify=args.verify)
    except AccountsRestoreError as exc:
        print(exc.message)
        if exc.extra:
            print(exc.extra)
        if exc.code == "accounts_url_unset":
            return 2
        if exc.code == "target_not_empty":
            return 2
        return 1

    print(f"written: {report.inserted}")
    print(f"skipped: {report.skipped}")
    print(f"emails_migrated: {report.emails_migrated}")
    if report.email_conflicts:
        print(f"email_conflicts: {report.email_conflicts}")
    if args.verify:
        print(f"verified: {report.postgres_counts_after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
