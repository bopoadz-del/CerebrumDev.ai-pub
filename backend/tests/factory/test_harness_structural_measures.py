"""The stamped harness measures STRUCTURE, never prose.

audit_clean: no execute-family call -- or a function that forwards a
parameter into one -- receives a dynamically built string (f-string with
fields, + or % with a string side, .format(), or a local bound to one).
health_fail_closed: app/health.py takes its connection from app.db and runs
a probe on it -- read from imports and calls. All code below is invented.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

from app.factory.build import acceptance_floor
from app.factory.build.deploy import render_health
from app.factory.build.store_acceptance import render_acceptance_script


def _harness(root: Path):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "acceptance.py").write_text(render_acceptance_script(None), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("acc_%d" % id(root), root / "scripts" / "acceptance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: Path, files: dict) -> Path:
    root = tmp_path / "product"
    for rel, src in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src), encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "call",
    [
        'conn.execute(f"SELECT * FROM {zorblat}")',
        'conn.execute("SELECT * FROM " + zorblat)',
        'conn.execute("DELETE FROM %s" % zorblat)',
        'conn.execute("UPDATE {} SET x = 1".format(zorblat))',
        'text(f"SELECT {zorblat}")',
    ],
)
def test_a_dynamically_built_statement_is_caught(tmp_path, call):
    root = _tree(tmp_path, {"app/quux.py": f"def run(conn, zorblat):\n    return {call}\n"})
    assert _harness(root)._dynamic_sql_sites() == ["app/quux.py:2"]


def test_a_local_name_bound_to_a_built_string_is_caught(tmp_path):
    root = _tree(tmp_path, {"app/quux.py": """
        def run(conn, zorblat):
            sql = "SELECT * FROM " + zorblat
            sql += " WHERE a = ?"
            return conn.execute(sql, (1,))
    """})
    assert _harness(root)._dynamic_sql_sites() == ["app/quux.py:5"]


def test_a_writers_own_wrapper_is_followed(tmp_path):
    root = _tree(tmp_path, {"app/quux.py": """
        def _run_it(conn, statement, params):
            return conn.execute(statement, params)

        def fetch(conn, zorblat):
            return _run_it(conn, f"SELECT * FROM {zorblat}", ())
    """})
    assert _harness(root)._dynamic_sql_sites() == ["app/quux.py:6"]


def test_core_statements_and_constants_with_bound_params_pass(tmp_path):
    root = _tree(tmp_path, {"app/quux.py": """
        import sqlalchemy as sa

        CONSTANT = "SELECT * FROM zorblat WHERE id = ?"

        def run(conn, table, zorblat_id):
            conn.execute("SELECT 1")
            conn.execute(CONSTANT, (zorblat_id,))
            conn.execute(sa.select(table).where(table.c.id == zorblat_id))
            compiled = sa.select(table).compile()
            return conn.execute(str(compiled), [zorblat_id])
    """})
    assert _harness(root)._dynamic_sql_sites() == []


def test_the_shipped_health_probes_through_app_db(tmp_path):
    root = _tree(tmp_path, {"app/health.py": render_health()})
    probes, why = _harness(root)._health_probes_app_db()
    assert probes is True, why


@pytest.mark.parametrize(
    "src, reason",
    [
        ("import sqlite3\n\ndef evaluate_health():\n    sqlite3.connect('x').execute('SELECT 1')\n",
         "does not take its database connection from app.db"),
        ("from app import db\n\ndef evaluate_health():\n    return db.connect()\n",
         "never runs a probe"),
    ],
)
def test_a_health_that_skips_app_db_or_the_probe_fails(tmp_path, src, reason):
    root = _tree(tmp_path, {"app/health.py": src})
    probes, why = _harness(root)._health_probes_app_db()
    assert probes is False and reason in why


def test_a_check_with_a_gate_but_no_brief_line_refuses_to_load(tmp_path, monkeypatch):
    """Drift guard: the brief and the gate read one file, so a check the gate
    runs with nothing to tell the writer is refused when the floor loads."""
    data = json.loads(acceptance_floor.floor_path().read_text(encoding="utf-8"))
    invented = dict(data["checks"][0])
    invented.update(id="zorblat_drift_check", gate_fn="check_zorblat", brief_render="  ")
    data["checks"].append(invented)
    floor = tmp_path / "floor.json"
    floor.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(acceptance_floor, "floor_path", lambda: floor)
    acceptance_floor._load.cache_clear()
    try:
        with pytest.raises(ValueError, match="zorblat_drift_check has no brief_render"):
            acceptance_floor._load()
    finally:
        monkeypatch.undo()
        acceptance_floor._load.cache_clear()


def test_every_gated_check_on_the_real_floor_tells_the_writer_something():
    for check in acceptance_floor._load()["checks"]:
        if str(check.get("gate_fn") or "").strip():
            assert str(check.get("brief_render") or "").strip(), check["id"]
