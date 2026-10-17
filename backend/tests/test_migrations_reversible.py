"""Migrations must be reversible: upgrade → downgrade → upgrade round-trips."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]


def _alembic(args, db_url):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/root",
        "ENV": "test",
        "ACCOUNTS_DATABASE_URL": db_url,
        "PYTHONPATH": str(ROOT),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _tables(db_url):
    eng = sa.create_engine(db_url)
    try:
        return set(sa.inspect(eng).get_table_names())
    finally:
        eng.dispose()


def test_usage_counters_migration_round_trips(tmp_path):
    db = tmp_path / "accounts.db"
    url = f"sqlite:///{db}"

    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, up.stderr
    assert "usage_counters" in _tables(url), "upgrade did not create usage_counters"

    # Downgrade to the revision before usage_counters (0003 now sits on
    # head; ``-1`` would only drop billing columns).
    down = _alembic(["downgrade", "0001"], url)
    assert down.returncode == 0, down.stderr
    assert "usage_counters" not in _tables(url), "downgrade did not drop usage_counters"

    # And re-upgrade restores it — the migration is idempotent end to end.
    reup = _alembic(["upgrade", "head"], url)
    assert reup.returncode == 0, reup.stderr
    assert "usage_counters" in _tables(url)
