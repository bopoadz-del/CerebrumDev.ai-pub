"""A platform with DATABASE_URL set must LIVE on that database — all of it.

The live failure this file pins (build sess_*, 2026-09-27):

    WRITER failed — writer_behaviour_failed: workspace schema or migration
    failed: ObjectNotExecutableError: Not an executable object:
    'SELECT version_num FROM alembic_version'

Three templated files and one writer-authored file each answered "which
database?" separately, and two of them disagreed with the other two:

  * app/db.py (templated)      — DATABASE_URL set means Postgres via
                                  SQLAlchemy; absent means stdlib sqlite3.
  * app/store.py (writer)      — the floor REQUIRES it to take its
                                  connection from app.db.
  * app/migrations.py (templated) — raw-string ``conn.execute`` and
                                  ``except sqlite3.OperationalError``:
                                  sqlite-only, crashes on a SQLAlchemy
                                  connection. The reported error.
  * alembic/env.py (templated) — hardcoded ``sqlite:///`` with a comment
                                  admitting the duplication. DATABASE_URL
                                  read by the app and IGNORED by the
                                  migration — the exact failure
                                  postgres_boot_200's requirement text
                                  forbids in as many words.

So with DATABASE_URL set the platform migrated SQLite, served Postgres, and
checked its revision in Postgres. The crash was the symptom; the split brain
was the defect. These tests run the EMITTED modules in a subprocess, the way
the writer-behaviour probe does, in both boot modes.

DATABASE_URL here is a SQLAlchemy sqlite URL, on purpose: ``is_postgres()``
branches on the variable being set, not on the scheme, so this exercises the
exact SQLAlchemy code path that crashed — with no Postgres server needed and
no risk of touching one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.factory.test_writer_behaviour_gate import (
    _write_appointment_sql_workspace,
)

#: What a floor-compliant WRITER produces: store.py routing through app.db.
#: The templated store is stdlib sqlite3, but the acceptance floor tells the
#: writer "app/store.py MUST obtain its connection from app.db" — so the
#: build that crashed had THIS shape, and the harness must model it or the
#: red run would not reproduce the field failure.
_FLOOR_COMPLIANT_STORE = (
    "from app.db import connect as connect\n"
    "from app.db import sqlite_path as _sqlite_path\n"
    "\n"
    "\n"
    "def db_path():\n"
    "    return _sqlite_path()\n"
)

_DRIVER = (
    "import json\n"
    "import app.migrations as m\n"
    "rev = m.upgrade_head()\n"
    "print(json.dumps({'rev': rev, 'cur': m.current_revision()}))\n"
)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    _write_appointment_sql_workspace(root)  # writes app/db.py: the engine switch
    (root / "app" / "store.py").write_text(_FLOOR_COMPLIANT_STORE, encoding="utf-8")
    return root


def _run(root: Path, *, database_url: str | None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    env.pop("DATABASE_URL", None)
    env["STORAGE_PATH"] = str(root / "data")
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", _DRIVER],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_sqlite_mode_still_migrates_onto_storage_path(tmp_path):
    """Both boot modes, mode one: no DATABASE_URL means sqlite under
    STORAGE_PATH, exactly as before. The fix must not move the default."""
    root = _workspace(tmp_path)
    proc = _run(root, database_url=None)
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["rev"], "upgrade_head reported no revision"
    assert out["cur"] == out["rev"]
    assert (root / "data" / "platform.db").is_file(), (
        "sqlite mode stopped writing the STORAGE_PATH database"
    )


def test_an_operator_database_url_is_where_the_platform_actually_lives(tmp_path):
    """Both boot modes, mode two — the one that failed live.

    With DATABASE_URL set: upgrade_head() must not crash, the alembic
    version must land in the DATABASE_URL database, current_revision() must
    read it back from there, and NO sqlite file may appear under
    STORAGE_PATH — a platform.db materialising here is the split brain
    itself, a second database the operator does not know exists.
    """
    root = _workspace(tmp_path)
    operator_db = tmp_path / "operator" / "operator.db"
    operator_db.parent.mkdir(parents=True)
    url = "sqlite:///" + operator_db.resolve().as_posix()

    proc = _run(root, database_url=url)

    assert "ObjectNotExecutableError" not in (proc.stderr or ""), (
        "the sqlite-only revision read met a SQLAlchemy connection again:\n"
        + proc.stderr[-800:]
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["rev"], "upgrade_head reported no revision"
    assert out["cur"] == out["rev"]

    assert operator_db.is_file(), (
        "DATABASE_URL was set and the migration did not touch its target"
    )
    conn = sqlite3.connect(str(operator_db))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    assert row and row[0] == out["rev"], (
        "the alembic version is not in the operator's database — it went "
        "somewhere else"
    )
    assert not (root / "data" / "platform.db").exists(), (
        "a STORAGE_PATH sqlite file appeared while DATABASE_URL was set: the "
        "operator believes they are on their database while the platform "
        "keeps a second one they cannot see"
    )


def test_gate_subprocesses_do_not_inherit_the_factorys_database_url(tmp_path, monkeypatch):
    """The trigger, as distinct from the defect.

    The writer-behaviour probe runs the workspace's migration in a
    subprocess built from ``{**os.environ, ...}`` — so the FACTORY's own
    DATABASE_URL (a Render-era injection that rode into the AWS secrets)
    became the PRODUCT's database for the duration of a gate. With env.py
    fixed to honour DATABASE_URL, that inheritance would point a product's
    migrations at the Factory's database, which is strictly worse than the
    crash it used to cause. A gate subprocess must never see it.
    """
    from app.factory.build.gates import _real_run

    monkeypatch.setenv("DATABASE_URL", "postgresql://factory-own-db/leak")
    proc = _real_run(
        [
            sys.executable,
            "-c",
            "import os; print('leaked' if 'DATABASE_URL' in os.environ else 'clean')",
        ],
        cwd=tmp_path,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "clean", (
        "the gate subprocess inherited the Factory's DATABASE_URL"
    )
