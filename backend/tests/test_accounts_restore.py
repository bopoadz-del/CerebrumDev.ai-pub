"""Master-key SQLite → Postgres accounts restore (merge, no wipe)."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.core import accounts_store
from scripts.migrate_accounts_to_postgres import (
    AccountsRestoreError,
    compare_stores,
    main as migrate_main,
    run_restore,
)

OWNER_ID = "acct_c38ae401deadbeef"
OWNER_EMAIL = "owner.m@projecteleven"
SMOKE_EMAIL = "factory-smoke-a@cerebrum-dev.invalid"
OWNER_PASSWORD = "owner-restore-pass-123"


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


def _configure_paths(monkeypatch, tmp_path, *, postgres_url: str | None):
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    if postgres_url:
        monkeypatch.setenv("ACCOUNTS_DATABASE_URL", postgres_url)
    else:
        monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    accounts_store._ENGINES.clear()
    return storage / "accounts.db"


def test_run_restore_refuses_unset_accounts_url(tmp_path, monkeypatch):
    sqlite = _configure_paths(monkeypatch, tmp_path, postgres_url=None)
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    with pytest.raises(AccountsRestoreError) as exc:
        run_restore(force=True)
    assert exc.value.code == "accounts_url_unset"


def test_run_restore_inserts_into_empty_target(tmp_path, monkeypatch):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    dest = sa.create_engine(f"sqlite:///{target}")
    accounts_store._META.create_all(dest, checkfirst=True)
    dest.dispose()

    report = run_restore(force=False, verify=True)
    assert report.ok is True
    assert report.mode == "insert"
    assert report.inserted["accounts"] == 1
    assert OWNER_EMAIL in report.emails_migrated
    assert report.postgres_counts_before.get("accounts", 0) == 0
    assert report.postgres_counts_after["accounts"] == 1
    dumped = json.dumps(report.as_public_dict())
    assert "password_hash" not in dumped
    assert "pbkdf2" not in dumped


def test_run_restore_refuses_nonempty_without_force(tmp_path, monkeypatch):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()

    with pytest.raises(AccountsRestoreError) as exc:
        run_restore(force=False)
    assert exc.value.code == "target_not_empty"


def test_force_merges_missing_owner_without_wiping_smoke(tmp_path, monkeypatch):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()

    report = run_restore(force=True, verify=True)
    assert report.ok is True
    assert report.mode == "merge"
    assert report.inserted["accounts"] == 1
    assert report.emails_migrated == [OWNER_EMAIL]
    assert report.postgres_counts_before["accounts"] == 1
    assert report.postgres_counts_after["accounts"] == 2

    dest = sa.create_engine(f"sqlite:///{target}")
    with dest.connect() as conn:
        rows = {
            r[0]: r[1]
            for r in conn.execute(
                sa.text("SELECT id, email FROM accounts")
            ).fetchall()
        }
        owner_hash = conn.execute(
            sa.text("SELECT password_hash FROM accounts WHERE id = :id"),
            {"id": OWNER_ID},
        ).scalar_one()
    dest.dispose()
    assert rows[OWNER_ID] == OWNER_EMAIL
    assert rows["acct_smoke1"] == SMOKE_EMAIL
    assert owner_hash.startswith("pbkdf2$")
    assert accounts_store._verify_password(OWNER_PASSWORD, owner_hash)


def test_force_is_idempotent_and_reports_skipped_existing(tmp_path, monkeypatch):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    dest = sa.create_engine(f"sqlite:///{target}")
    accounts_store._META.create_all(dest, checkfirst=True)
    dest.dispose()

    first = run_restore(force=True, verify=True)
    second = run_restore(force=True, verify=True)
    assert first.inserted["accounts"] == 1
    assert second.inserted["accounts"] == 0
    assert second.skipped["accounts"] == 1
    assert second.emails_skipped_existing == [OWNER_EMAIL]
    assert second.postgres_counts_after["accounts"] == 1


def test_email_conflict_does_not_overwrite_existing_id(tmp_path, monkeypatch):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_other", OWNER_EMAIL, "other-pass-123")
    dest.dispose()

    report = run_restore(force=True, verify=True)
    assert report.inserted["accounts"] == 0
    assert report.email_conflicts == [
        {
            "sqlite_id": OWNER_ID,
            "sqlite_email": OWNER_EMAIL,
            "postgres_id": "acct_other",
        }
    ]
    dest = sa.create_engine(f"sqlite:///{target}")
    with dest.connect() as conn:
        ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM accounts")).fetchall()]
    dest.dispose()
    assert ids == ["acct_other"]


def test_compare_stores_lists_ids_and_emails_only(tmp_path, monkeypatch):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()

    listing = compare_stores()
    assert listing["sqlite_accounts"] == [{"id": OWNER_ID, "email": OWNER_EMAIL}]
    assert listing["postgres_accounts"] == [
        {"id": "acct_smoke1", "email": SMOKE_EMAIL}
    ]
    dumped = json.dumps(listing)
    assert "password_hash" not in dumped
    assert "pbkdf2" not in dumped
    assert "cerebrum-builds" in listing["note"]


def test_cli_force_merges_and_verify_exit_zero(tmp_path, monkeypatch, capsys):
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()

    assert migrate_main(["--force", "--verify"]) == 0
    out = capsys.readouterr().out
    assert OWNER_EMAIL in out
    assert "password_hash" not in out
    assert migrate_main([]) == 2  # non-empty, no --force


def test_http_restore_requires_master_key(client, monkeypatch, tmp_path):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    assert client.get("/v1/ops/accounts-restore").status_code == 404
    assert client.post("/v1/ops/accounts-restore").status_code == 404


def test_http_restore_rejects_wrong_key(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.get(
        "/v1/ops/accounts-restore",
        headers={"Authorization": "Bearer nope"},
    )
    assert res.status_code == 401


def test_http_restore_refuses_unset_url(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    sqlite = _configure_paths(monkeypatch, tmp_path, postgres_url=None)
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()

    res = client.post(
        "/v1/ops/accounts-restore?force=true",
        headers={"Authorization": "Bearer master-secret"},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "accounts_url_unset"


def test_http_get_and_force_merge(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    target = tmp_path / "postgres.db"
    sqlite = _configure_paths(
        monkeypatch, tmp_path, postgres_url=f"sqlite:///{target}"
    )
    src = sa.create_engine(f"sqlite:///{sqlite}")
    _seed_account(src, OWNER_ID, OWNER_EMAIL, OWNER_PASSWORD)
    src.dispose()
    dest = sa.create_engine(f"sqlite:///{target}")
    _seed_account(dest, "acct_smoke1", SMOKE_EMAIL, "smoke-pass-123")
    dest.dispose()

    headers = {"Authorization": "Bearer master-secret"}
    listing = client.get("/v1/ops/accounts-restore", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["sqlite_accounts"][0]["id"] == OWNER_ID
    assert "password_hash" not in listing.text

    refused = client.post("/v1/ops/accounts-restore", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "target_not_empty"

    merged = client.post(
        "/v1/ops/accounts-restore?force=true",
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["ok"] is True
    assert body["emails_migrated"] == [OWNER_EMAIL]
    assert body["postgres_counts_before"]["accounts"] == 1
    assert body["postgres_counts_after"]["accounts"] == 2
    assert "password_hash" not in merged.text
    assert "pbkdf2" not in merged.text

    again = client.post(
        "/v1/ops/accounts-restore?force=true",
        headers=headers,
    )
    assert again.status_code == 200
    assert again.json()["inserted"]["accounts"] == 0
    assert again.json()["emails_skipped_existing"] == [OWNER_EMAIL]
