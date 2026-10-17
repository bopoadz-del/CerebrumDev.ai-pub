"""Master-key restore of accounts from on-disk backup archives."""

from __future__ import annotations

import json
import secrets
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core import accounts_store, backup as bk
from app.core.accounts_backup_restore import (
    list_backup_archives,
    load_accounts_data,
    read_legacy_sql_dump,
    read_structured_snapshot,
    run_restore_from_backup,
)
from scripts.migrate_accounts_to_postgres import (
    AccountsRestoreError,
    run_restore,
)

OWNER_ID = "acct_c38ae401f79a4fbc"
OWNER_EMAIL = "owner.m@projecteleven"
SMOKE_EMAIL = "factory-smoke-a@cerebrum-dev.invalid"
LIVE_CONFLICT_ID = "acct_25e47fc0cafef00d"
OWNER_PASSWORD = "owner-backup-pass-123"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_account(engine: sa.engine.Engine, account_id: str, email: str, password: str) -> None:
    accounts_store._META.create_all(engine, checkfirst=True)
    salt = secrets.token_hex(16)
    with engine.begin() as conn:
        conn.execute(
            accounts_store._t_accounts.insert().values(
                id=account_id,
                email=email,
                password_hash=accounts_store._hash_password(password, salt),
                email_verified=True,
                trial_ends_at=None,
                subscription_status="active",
                created_at=_now(),
            )
        )


def _configure(monkeypatch, tmp_path, *, postgres_url: str | None):
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    backups = tmp_path / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("BACKUP_DIR", str(backups))
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    if postgres_url:
        monkeypatch.setenv("ACCOUNTS_DATABASE_URL", postgres_url)
    else:
        monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    accounts_store._ENGINES.clear()
    return storage, backups


def _write_archive(backups: Path, stamp: str, files: dict[str, bytes]) -> Path:
    archive = backups / f"cerebrumdev-backup-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in files.items():
            member = backups / name
            member.write_bytes(payload)
            tar.add(member, arcname=name)
            member.unlink()
    return archive


def _owner_db(tmp_path: Path) -> Path:
    src = tmp_path / "dump-src.db"
    engine = sa.create_engine(f"sqlite:///{src}")
    _seed_account(engine, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    engine.dispose()
    return src


def _owner_snapshot(tmp_path: Path) -> bytes:
    """The structured accounts.json every new archive carries."""
    src = _owner_db(tmp_path)
    out = tmp_path / "accounts.json"
    bk.snapshot_postgres_via_sqlalchemy(f"sqlite:///{src}", out)
    return out.read_bytes()


def _owner_legacy_sql(tmp_path: Path) -> bytes:
    """A legacy SQL-INSERT dump, produced by a real SQL engine (iterdump)."""
    import sqlite3

    src = _owner_db(tmp_path)
    conn = sqlite3.connect(src)
    try:
        return ("\n".join(conn.iterdump()) + "\n").encode("utf-8")
    finally:
        conn.close()


def test_structured_snapshot_round_trips_the_rows(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_bytes(_owner_snapshot(tmp_path))
    data = read_structured_snapshot(path)
    row = data["accounts"][0]
    assert row["id"] == OWNER_ID and row["email"] == OWNER_EMAIL
    assert row["email_verified"] is True
    assert "password_hash" in row


def test_a_legacy_sql_dump_is_read_by_a_sql_engine_not_parsed(tmp_path):
    path = tmp_path / "accounts.sql"
    path.write_bytes(_owner_legacy_sql(tmp_path))
    data = read_legacy_sql_dump(path)
    row = data["accounts"][0]
    assert row["id"] == OWNER_ID and row["email"] == OWNER_EMAIL
    assert row["email_verified"] is True


def test_a_custom_pg_dump_names_the_operator_tool(tmp_path):
    path = tmp_path / "accounts.dump"
    path.write_bytes(b"PGDMP\x01\x0e\x00 invented custom-format header")
    with pytest.raises(AccountsRestoreError) as exc:
        load_accounts_data(path)
    assert exc.value.code == "dump_requires_operator_tool"


def test_list_backups_flags_dump_and_peeks_emails(tmp_path, monkeypatch):
    _storage, backups = _configure(monkeypatch, tmp_path, postgres_url=None)
    _write_archive(
        backups,
        "20260912T030000Z",
        {"accounts.json": _owner_snapshot(tmp_path)},
    )
    listing = list_backup_archives()
    assert listing["recommended"] == "cerebrumdev-backup-20260912T030000Z.tar.gz"
    assert listing["archives"][0]["accounts_json"] is True
    assert listing["archives"][0]["source_accounts"] == [
        {"id": OWNER_ID, "email": OWNER_EMAIL}
    ]
    assert "password_hash" not in json.dumps(listing)


def test_from_backup_refuses_unset_url(tmp_path, monkeypatch):
    _storage, backups = _configure(monkeypatch, tmp_path, postgres_url=None)
    _write_archive(
        backups, "20260912T030000Z", {"accounts.json": _owner_snapshot(tmp_path)}
    )
    with pytest.raises(AccountsRestoreError) as exc:
        run_restore_from_backup(archive="cerebrumdev-backup-20260912T030000Z.tar.gz")
    assert exc.value.code == "accounts_url_unset"


def test_from_backup_rejects_path_traversal(tmp_path, monkeypatch):
    target = tmp_path / "pg.db"
    _configure(monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}")
    with pytest.raises(AccountsRestoreError) as exc:
        run_restore_from_backup(archive="../etc/passwd")
    assert exc.value.code == "archive_unsafe"


def test_from_backup_merges_sql_dump_without_wiping_smoke(tmp_path, monkeypatch):
    target = tmp_path / "pg.db"
    _storage, backups = _configure(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()
    _write_archive(
        backups, "20260912T030000Z", {"accounts.json": _owner_snapshot(tmp_path)}
    )

    report = run_restore_from_backup(
        archive="cerebrumdev-backup-20260912T030000Z.tar.gz",
        force=True,
        prefer_source=True,
    )
    assert report["ok"] is True
    assert report["dump_kind"] == "accounts.json"
    assert report["emails_migrated"] == [OWNER_EMAIL]
    assert report["postgres_counts_after"]["accounts"] == 2
    dest = sa.create_engine(f"sqlite:///{target}")
    with dest.connect() as conn:
        rows = {
            r[0]: r[1]
            for r in conn.execute(sa.text("SELECT id, email FROM accounts")).fetchall()
        }
    dest.dispose()
    assert rows[OWNER_ID] == OWNER_EMAIL
    assert rows["acct_smoke1"] == SMOKE_EMAIL
    assert "password_hash" not in json.dumps(report)
    assert "pbkdf2" not in json.dumps(report)


def test_prefer_source_displaces_conflicting_live_email(tmp_path, monkeypatch):
    target = tmp_path / "pg.db"
    _storage, backups = _configure(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, LIVE_CONFLICT_ID, OWNER_EMAIL, "later-register-pass")
    dest.dispose()
    _write_archive(
        backups, "20260912T030000Z", {"accounts.json": _owner_snapshot(tmp_path)}
    )

    report = run_restore_from_backup(
        archive="latest",
        force=True,
        prefer_source=True,
    )
    assert report["ok"] is True
    assert report["emails_migrated"] == [OWNER_EMAIL]
    assert report["emails_displaced"][0]["postgres_id"] == LIVE_CONFLICT_ID
    assert report["emails_displaced"][0]["original_email"] == OWNER_EMAIL
    dest = sa.create_engine(f"sqlite:///{target}")
    with dest.connect() as conn:
        rows = {
            r[0]: r[1]
            for r in conn.execute(sa.text("SELECT id, email FROM accounts")).fetchall()
        }
        owner_hash = conn.execute(
            sa.text("SELECT password_hash FROM accounts WHERE id = :id"),
            {"id": OWNER_ID},
        ).scalar_one()
    dest.dispose()
    assert rows[OWNER_ID] == OWNER_EMAIL
    assert rows[LIVE_CONFLICT_ID].startswith("displaced+")
    assert accounts_store._verify_password(OWNER_PASSWORD, owner_hash)


def test_session_owners_gap_is_advisory_not_verify_failed(tmp_path, monkeypatch):
    target = tmp_path / "pg.db"
    storage, _backups = _configure(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    sqlite = storage / "accounts.db"
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    accounts_store._META.create_all(src, checkfirst=True)
    with src.begin() as conn:
        conn.execute(
            accounts_store._t_session_owners.insert().values(
                session_id="sess_orphan",
                account_id="acct_not_in_dump",
                created_at=_now(),
            )
        )
    src.dispose()
    dest = sa.create_engine(f"sqlite:///{target}")
    accounts_store._META.create_all(dest, checkfirst=True)
    dest.dispose()

    report = run_restore(force=True, verify=True)
    assert report.ok is True
    assert report.verified is True
    assert report.inserted["accounts"] == 1
    assert "session_owners" in report.missing_advisory


def test_http_backup_endpoints(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    target = tmp_path / "pg.db"
    _storage, backups = _configure(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()
    _write_archive(
        backups, "20260912T030000Z", {"accounts.json": _owner_snapshot(tmp_path)}
    )
    headers = {"Authorization": "Bearer master-secret"}

    listing = client.get("/v1/ops/accounts-restore/backups", headers=headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["archives"][0]["name"].endswith("20260912T030000Z.tar.gz")
    assert body["archives"][0]["source_accounts"][0]["id"] == OWNER_ID
    assert "password_hash" not in listing.text

    merged = client.post(
        "/v1/ops/accounts-restore/from-backup?archive=cerebrumdev-backup-20260912T030000Z.tar.gz",
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["emails_migrated"] == [OWNER_EMAIL]
    assert "password_hash" not in merged.text


def test_http_backups_require_master_key(client, monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    assert client.get("/v1/ops/accounts-restore/backups").status_code == 404
    assert client.post("/v1/ops/accounts-restore/from-backup").status_code == 404


def test_from_backup_merges_a_legacy_sql_archive(tmp_path, monkeypatch):
    """Archives written before accounts_snapshot.v1 carry only accounts.sql:
    they still restore through the endpoint, read by a SQL engine."""
    target = tmp_path / "pg.db"
    _storage, backups = _configure(monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}")
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()
    _write_archive(backups, "20260912T030000Z", {"accounts.sql": _owner_legacy_sql(tmp_path)})

    report = run_restore_from_backup(
        archive="cerebrumdev-backup-20260912T030000Z.tar.gz", force=True, prefer_source=True
    )
    assert report["ok"] is True
    assert report["dump_kind"] == "accounts.sql"
    assert report["emails_migrated"] == [OWNER_EMAIL]
    assert report["postgres_counts_after"]["accounts"] == 2
