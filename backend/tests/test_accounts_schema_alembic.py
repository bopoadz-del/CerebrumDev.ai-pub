"""Alembic is the accounts schema source of truth (AUDIT.md L3).

Runtime ``_ensure_column`` must not ALTER TABLE. A test DB missing a column
applies migrations instead of mutating on first ``_engine()`` call.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from app.core import accounts_store
from app.core.accounts_migrations import apply_accounts_migrations

_LEGACY_COLUMNS = (
    "reset_token_hash",
    "reset_expires_at",
    "trial_ends_at",
    "subscription_status",
    "stripe_customer_id",
    "stripe_subscription_id",
)


def _legacy_accounts_db(path) -> None:
    """Pre-billing accounts table: the shape ``_ensure_column`` used to patch."""
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE accounts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    verify_token_hash TEXT,
                    verify_expires_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
    engine.dispose()


def _account_columns(path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns("accounts")}
    finally:
        engine.dispose()


def test_ensure_column_logs_and_does_not_alter(tmp_path, caplog):
    db = tmp_path / "legacy.db"
    _legacy_accounts_db(db)
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        with caplog.at_level(logging.WARNING, logger="app.core.accounts_store"):
            accounts_store._ensure_column(
                conn, "accounts", "trial_ends_at", "trial_ends_at TEXT"
            )
    engine.dispose()

    assert "trial_ends_at" not in _account_columns(db)
    assert any(
        "_ensure_column is disabled" in rec.getMessage()
        and "trial_ends_at" in rec.getMessage()
        for rec in caplog.records
    )


def test_engine_does_not_add_missing_columns(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    _legacy_accounts_db(db)
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    accounts_store._ENGINES.clear()
    try:
        accounts_store._engine()
        missing = [c for c in _LEGACY_COLUMNS if c not in _account_columns(db)]
        assert missing == list(_LEGACY_COLUMNS)
    finally:
        accounts_store._ENGINES.clear()


def test_apply_migrations_does_not_reset_process_logging(
    tmp_path, monkeypatch, caplog
):
    """Alembic fileConfig must not wipe pytest/app handlers (CI caplog leak)."""
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/fresh.db")
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    apply_accounts_migrations()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("app.factory.coder").warning(
            "cross-provider fallback not armed: paid model refused"
        )
    assert "not armed" in caplog.text


def test_missing_columns_are_added_by_alembic_not_runtime(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    _legacy_accounts_db(db)
    url = f"sqlite:///{db}"
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", url)
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    accounts_store._ENGINES.clear()
    try:
        accounts_store._engine()
        before = _account_columns(db)
        assert "trial_ends_at" not in before
        assert "stripe_customer_id" not in before

        apply_accounts_migrations()

        after = _account_columns(db)
        for column in _LEGACY_COLUMNS:
            assert column in after, f"alembic head did not add {column}"
    finally:
        accounts_store._ENGINES.clear()
