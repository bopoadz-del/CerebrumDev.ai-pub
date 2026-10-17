"""Boot must survive a STORAGE_PATH whose directory does not exist yet.

New-shape test for the 2026-08-11 outage: the Dockerfile CMD runs
``alembic upgrade head`` before uvicorn, and alembic/env.py resolved the
SQLite URL without creating the parent directory. On Render that directory
was always pre-created by the mounted disk, so the gap was invisible --
until the service was deployed with no disk attached and every boot died on
``sqlite3.OperationalError: unable to open database file``.

These drive the real entrypoint in a subprocess rather than importing
env.py, because env.py only evaluates inside an alembic context.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_migrations(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # A stale Postgres URL or db-path override in the ambient environment
    # would route around the SQLite branch under test.
    for stale in ("ACCOUNTS_DATABASE_URL", "ACCOUNTS_DB_PATH", "STORAGE_PATH"):
        env.pop(stale, None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_migrations_create_missing_storage_dir(tmp_path):
    """The exact production shape: STORAGE_PATH points at nothing on disk."""
    storage = tmp_path / "app" / "storage"
    assert not storage.exists()

    result = _run_migrations({"STORAGE_PATH": str(storage)})

    assert result.returncode == 0, result.stderr
    assert "unable to open database file" not in result.stderr
    assert (storage / "accounts.db").is_file()


def test_migrations_create_missing_parent_of_db_path_override(tmp_path):
    """ACCOUNTS_DB_PATH takes the same path and needs the same guarantee."""
    db_path = tmp_path / "nested" / "deeper" / "accounts.db"

    result = _run_migrations({"ACCOUNTS_DB_PATH": str(db_path)})

    assert result.returncode == 0, result.stderr
    assert db_path.is_file()


@pytest.mark.parametrize("attempt", (1, 2))
def test_migrations_are_idempotent_on_existing_dir(tmp_path, attempt):
    """mkdir(exist_ok) must not regress the normal mounted-disk path."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)

    result = _run_migrations({"STORAGE_PATH": str(storage)})

    assert result.returncode == 0, result.stderr
    assert (storage / "accounts.db").is_file()


def test_postgres_url_resolves_the_installed_driver(tmp_path):
    """New-shape test for the 2026-08-14 deploy failure: Render hands out
    ``postgresql://`` and env.py only upgraded the short ``postgres://``
    prefix, so SQLAlchemy defaulted to the psycopg2 dialect and the boot
    migration died on ModuleNotFoundError (psycopg3 is what's installed).

    No database server needed: the old code crashes on the missing driver
    BEFORE any connection attempt, the fixed code must get past dialect
    loading and fail on the unreachable host instead."""
    result = _run_migrations(
        {"ACCOUNTS_DATABASE_URL": "postgresql://u:p@127.0.0.1:9/none"}
    )

    assert result.returncode != 0, "connecting to port 9 cannot succeed"
    assert "No module named 'psycopg2'" not in result.stderr, (
        "the postgresql:// prefix fell through to the psycopg2 dialect again"
    )
    assert (
        "OperationalError" in result.stderr
        or "connection" in result.stderr.lower()
    ), result.stderr[-400:]
