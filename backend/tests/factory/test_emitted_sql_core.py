"""Emitted persistence builds SQL from the declared schema, never from strings.

Live 2026-10-04: the Store gate's audit_clean (bandit -r app/, fail on any
HIGH or B608) failed both repro builds -- two unrelated briefs -- on
string-built SQL in app/work_queue.py (emitted byte-for-byte by the Factory)
and app/store.py (seeded by the Factory). Both modules now build SQLAlchemy
Core statements from table/column objects and compile them for the backend
app.db is on. Entity and field names below are invented.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.factory.build.data_lifecycle import render_store
from app.factory.build.domain_acceptance import render_work_queue
from app.factory.build.engine_switch import render_db_module

SPECS = {
    "zorblat-intake": {
        "entity": "zorblat_item",
        "fields": [
            {"name": "quux_label", "type": "str"},
            {"name": "grade", "type": "int"},
        ],
    }
}

DDL = """
CREATE TABLE zorblat_item (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                           quux_label TEXT, grade INTEGER);
CREATE TABLE work_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, capability_id TEXT NOT NULL,
                         tenant_id TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                         result TEXT, idempotency_key TEXT);
CREATE TABLE idempotency (key TEXT PRIMARY KEY, entity TEXT NOT NULL, record_id INTEGER NOT NULL);
"""


def _product(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    app = root / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "db.py").write_text(render_db_module(), encoding="utf-8")
    (app / "store.py").write_text(render_store(SPECS), encoding="utf-8")
    (app / "work_queue.py").write_text(render_work_queue(), encoding="utf-8")
    return root


def _run(root: Path, body: str) -> dict:
    storage = root / "storage"
    storage.mkdir(exist_ok=True)
    env = {
        k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "PYTHONPATH")
    }
    env.update({"STORAGE_PATH": str(storage), "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"})
    script = "import json, sqlite3\n" + textwrap.dedent(body)
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_emitted_modules_carry_no_string_built_sql(tmp_path):
    if importlib.util.find_spec("bandit") is None:
        pytest.skip(reason="bandit not installed here; CI installs it")
    root = _product(tmp_path)
    report = root / "bandit.json"
    subprocess.run(
        [sys.executable, "-m", "bandit", "-r", "app/", "-f", "json", "-o", str(report), "-q"],
        cwd=root, capture_output=True, text=True,
    )
    results = json.loads(report.read_text(encoding="utf-8"))["results"]
    flagged = [
        f"{r['filename']}:{r['line_number']} {r['test_id']} {r['issue_severity']}"
        for r in results
        if r["test_id"] == "B608" or str(r["issue_severity"]).upper() == "HIGH"
    ]
    assert flagged == [], flagged


def test_store_round_trip_on_sqlite(tmp_path):
    root = _product(tmp_path)
    result = _run(root, f"""
        from app import store
        conn = sqlite3.connect(str(store.db_path()))
        conn.executescript({DDL!r}); conn.commit(); conn.close()
        a = store.save("zorblat_item", {{"quux_label": "b", "grade": 2}}, "t1")
        store.save("zorblat_item", {{"quux_label": "a", "grade": 1}}, "t1")
        store.save("zorblat_item", {{"quux_label": "z", "grade": 9}}, "t2")
        got = store.get("zorblat_item", a["id"], "t1")
        other = store.get("zorblat_item", a["id"], "t2")
        page = store.query("zorblat_item", "t1", sort="quux_label", order="desc", limit=10)
        filtered = store.query("zorblat_item", "t1", filters={{"grade": 1}})
        updated = store.update("zorblat_item", a["id"], {{"quux_label": "c", "grade": 3}}, "t1")
        removed_other = store.delete("zorblat_item", a["id"], "t2")
        removed = store.delete("zorblat_item", a["id"], "t1")
        refused = None
        try:
            store.query("zorblat_item", "t1", sort="not_declared")
        except store.QueryError as exc:
            refused = str(exc)
        print(json.dumps({{
            "id": a["id"], "got": got["quux_label"], "other": other,
            "order": [r["quux_label"] for r in page["items"]], "total": page["total"],
            "filtered": [r["quux_label"] for r in filtered["items"]],
            "updated": updated["quux_label"], "removed_other": removed_other,
            "removed": removed, "after": store.list_all("zorblat_item", "t1"),
            "refused": refused,
        }}))
    """)
    assert isinstance(result["id"], int)
    assert result["got"] == "b" and result["other"] is None
    assert result["order"] == ["b", "a"] and result["total"] == 2
    assert result["filtered"] == ["a"]
    assert result["updated"] == "c"
    assert result["removed_other"] is False and result["removed"] is True
    assert [r["quux_label"] for r in result["after"]] == ["a"]
    assert "not_declared" in (result["refused"] or "")


def test_work_queue_lifecycle_and_idempotency_on_sqlite(tmp_path):
    root = _product(tmp_path)
    result = _run(root, f"""
        from app import store, work_queue as q
        conn = sqlite3.connect(str(store.db_path()))
        conn.executescript({DDL!r}); conn.commit(); conn.close()
        item = q.enqueue("zorblat-intake", {{"k": 1}}, idempotency_key="key-1", tenant_id="t1")
        stranger = q.claim_pending(item["id"], tenant_id="t2")
        claimed = q.claim_pending(item["id"], tenant_id="t1")
        again = q.claim_pending(item["id"], tenant_id="t1")
        done = q.mark(item["id"], q.PROCESSED, {{"ok": True}}, from_status=q.PROCESSING)
        stale = q.mark(item["id"], q.FAILED, None, from_status=q.PROCESSING)
        q.remember("key-1", "zorblat_item", 7)
        q.remember("key-1", "zorblat_item", 8)
        print(json.dumps({{
            "payload": item["payload"], "status": item["status"],
            "stranger": stranger, "claimed": claimed["status"], "again": again,
            "done": done["status"], "result": done["result"], "stale": stale,
            "hidden": q.get(item["id"], tenant_id="t2"),
            "mine": [i["id"] for i in q.list_all(tenant_id="t1")],
            "theirs": q.list_all(tenant_id="t2"), "recall": q.recall("key-1"),
        }}))
    """)
    assert result["payload"] == {"k": 1} and result["status"] == "pending"
    assert result["stranger"] is None and result["claimed"] == "processing"
    assert result["again"] is None
    assert result["done"] == "processed" and result["result"] == {"ok": True}
    assert result["stale"] is None
    assert result["hidden"] is None and result["theirs"] == []
    assert len(result["mine"]) == 1
    assert result["recall"] == {"key": "key-1", "entity": "zorblat_item", "record_id": 8}
