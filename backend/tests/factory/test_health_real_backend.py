"""The emitted /health probes the database the product actually runs on.

Live 2026-10-04: both repro builds booted cleanly on Postgres in the Store
gate (migrations ran) and /health still answered 503 "platform.db missing"
-- the emitted check looked for the SQLite file whatever the backend.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.factory.build.deploy import render_health


class _Conn:
    def __init__(self, seen):
        self.seen = seen

    def execute(self, stmt):
        self.seen.append(str(stmt))

    def close(self):
        pass


def _load_health(monkeypatch, tmp_path, *, backend, sqlite_exists=True):
    seen = []
    app = types.ModuleType("app")
    app.__path__ = []
    db = types.ModuleType("app.db")
    db.backend_name = lambda: backend
    db.sqlite_path = lambda: tmp_path / "zorblat.db"
    db.connect = lambda: _Conn(seen)
    revision = types.ModuleType("app.revision")
    revision.current_app_mark = lambda: "m"
    revision.current_app_revision = lambda: "r"
    migrations = types.ModuleType("app.migrations")
    migrations.current_revision = lambda: "head"
    migrations.head_revision = lambda: "head"
    app.db = db
    for name, mod in (("app", app), ("app.db", db), ("app.revision", revision), ("app.migrations", migrations)):
        monkeypatch.setitem(sys.modules, name, mod)
    if sqlite_exists:
        (tmp_path / "zorblat.db").write_bytes(b"")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    health = types.ModuleType("zorblat_health")
    exec(compile(render_health(), "health.py", "exec"), health.__dict__)
    return health, seen


def test_postgres_answers_and_no_sqlite_file_is_required(monkeypatch, tmp_path):
    pytest.importorskip("sqlalchemy")
    health, seen = _load_health(monkeypatch, tmp_path, backend="postgres", sqlite_exists=False)
    code, body = health.evaluate_health()
    database = next(c for c in body["checks"] if c["name"] == "database")
    assert database == {"name": "database", "ok": True, "detail": "postgres"}
    assert seen == ["SELECT 1"]
    assert code == 200


def test_sqlite_without_its_file_is_refused_by_name(monkeypatch, tmp_path):
    health, _ = _load_health(monkeypatch, tmp_path, backend="sqlite", sqlite_exists=False)
    code, body = health.evaluate_health()
    database = next(c for c in body["checks"] if c["name"] == "database")
    assert database["ok"] is False and database["detail"] == "zorblat.db missing"
    assert code == 503


def test_sqlite_with_its_file_answers(monkeypatch, tmp_path):
    health, seen = _load_health(monkeypatch, tmp_path, backend="sqlite")
    code, body = health.evaluate_health()
    assert code == 200 and seen == ["SELECT 1"]
