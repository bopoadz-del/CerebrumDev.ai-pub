"""The Store gate's concurrency floor: every write path holds at 2x the pool.

Live 2026-10-10 (cycle 9 smoke B, build/plt_f9c91b85bce748c5): ``save()``
held a pooled connection and called ``get()``, which checked out a second
one; twenty threads under the default pool (5 + 10) ended in a QueuePool
timeout. Only the TESTER's emitted suite caught it -- the Store gate had no
line that writes twice at once. ``concurrent_writes_hold`` is that line.

These tests run the RENDERED harness in a subprocess against an invented
product: a real SQLAlchemy pool on a SQLite file, a store whose ``save()``
either releases its connection before reading back (correct) or holds it
(the live defect), and a fake HTTP surface that calls the store the way a
sync route does -- or serialised, the way an ``async def`` route calling a
blocking store does (smoke B's route).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.acceptance_floor import check_ids, checks, enforced_ids
from app.factory.build.concurrency_floor import DEFAULT_POOL_CAPACITY, POOL_FACTOR
from app.factory.build.store_acceptance import render_acceptance_script

CHECK = "concurrent_writes_hold"

DRIVER = textwrap.dedent(
    '''
    import importlib.util, json, os, sys, threading
    from pathlib import Path

    root = Path(sys.argv[1])
    mode = sys.argv[2]
    sys.path.insert(0, str(root))
    os.environ["STORAGE_PATH"] = str(root / "data")
    spec = importlib.util.spec_from_file_location("acc", root / "scripts" / "acceptance.py")
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)

    from app import store
    if mode != "width":
        store.METADATA.create_all(store.db.engine())
    serial = threading.Lock()


    class Resp:
        def __init__(self, status, body, headers=None):
            self.status_code = status
            self._body = body
            self.headers = headers or {}

        def json(self):
            return self._body


    class Http:
        """A route over the store. 'sync': each request on the caller's
        thread (FastAPI's threadpool). 'async': every request serialised, as
        an async def route that calls a blocking store is on the loop."""

        def request(self, method, path, json=None, headers=None, **_kw):
            if mode == "async":
                with serial:
                    return self._answer(method, path, json, headers)
            return self._answer(method, path, json, headers)

        def _answer(self, method, path, json, headers):
            tenant = (headers or {}).get("Authorization", "").split()[-1]
            parts = path.strip("/").split("/")
            entity = "ledger_rows"
            try:
                if method == "post":
                    if mode == "dedupe":
                        return Resp(200, {"ok": True, "stored": {"id": 1}})
                    row = store.save(entity, dict(json or {}), tenant_id=tenant)
                    return Resp(200, {"ok": True, "stored": row})
                row = store.get(entity, int(parts[2]), tenant_id=tenant)
                return Resp(200 if row else 404, row or {})
            except Exception as exc:
                return Resp(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})


    if mode == "width":
        n, why = acc._concurrency_width()
        print(json.dumps({"n": n, "why": why}))
    else:
        status, detail = acc.check_concurrent_writes_hold(Http())
        print(json.dumps({"status": status, "detail": detail}))
    '''
)

MODELS = textwrap.dedent(
    """
    from dataclasses import dataclass

    @dataclass
    class Entry:
        reference: str = ""
        FIELDS = ["reference"]
        CONSTRAINTS = {"reference": {"required": True}}

    MODELS = {"ledger_entry": Entry}
    """
)

DB = textwrap.dedent(
    """
    import os
    from pathlib import Path

    from sqlalchemy import create_engine

    _ENGINE = []


    def engine():
        if __ENGINE_RAISES__:
            raise RuntimeError("DATABASE_URL is not set")
        if not _ENGINE:
            path = Path(os.environ["STORAGE_PATH"]) / "platform.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            _ENGINE.append(create_engine(
                "sqlite:///" + str(path),
                connect_args={"timeout": 30, "check_same_thread": False},
                pool_size=__POOL_SIZE__, max_overflow=__MAX_OVERFLOW__, pool_timeout=2,
            ))
        return _ENGINE[0]
    """
)

STORE = textwrap.dedent(
    """
    from sqlalchemy import Column, Integer, MetaData, Table, Text, func, insert, select

    from app import db

    FASTAPI_SYNC_THREADPOOL = __THREADPOOL__
    METADATA = MetaData()
    ROWS = Table("ledger_rows", METADATA,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("tenant_id", Text), Column("reference", Text))


    def _conn():
        return db.engine().connect()


    def save(entity, record, tenant_id):
        conn = _conn()
        try:
            new_id = conn.execute(insert(ROWS).values(
                tenant_id=tenant_id, reference=str(record.get("reference")))).inserted_primary_key[0]
            conn.commit()
            if __HOLDS__:
                # The live defect: read back on a SECOND pooled connection
                # while this one is still checked out.
                return get(entity, new_id, tenant_id)
        finally:
            conn.close()
        return get(entity, new_id, tenant_id)


    def get(entity, record_id, tenant_id):
        conn = _conn()
        try:
            row = conn.execute(select(ROWS).where(
                ROWS.c.id == record_id, ROWS.c.tenant_id == tenant_id)).mappings().first()
        finally:
            conn.close()
        return dict(row) if row else None


    def list_all(entity, tenant_id):
        conn = _conn()
        try:
            rows = conn.execute(select(ROWS).where(ROWS.c.tenant_id == tenant_id)).mappings().all()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    """
)


def _product(
    tmp_path: Path,
    *,
    holds: bool,
    pool_size: int = 2,
    max_overflow: int = 1,
    threadpool: int = 4,
    engine_raises: bool = False,
) -> Path:
    root = tmp_path / "product"
    (root / "app").mkdir(parents=True)
    (root / "scripts").mkdir()
    files = {
        "app/__init__.py": "",
        "app/models.py": MODELS,
        "app/routes.py": "ROUTE_ENTITIES = {'ledger_entry': 'ledger_rows'}\n",
        "app/jobs.py": "CAPABILITIES = [{'id': 'ledger_entry'}]\n",
        "app/placeholders.py": "PLACEHOLDER_CONNECTORS = {}\n",
        "app/db.py": DB.replace("__POOL_SIZE__", str(pool_size))
        .replace("__MAX_OVERFLOW__", str(max_overflow))
        .replace("__ENGINE_RAISES__", repr(engine_raises)),
        "app/store.py": STORE.replace("__HOLDS__", repr(holds)).replace(
            "__THREADPOOL__", str(threadpool)
        ),
        "scripts/acceptance.py": render_acceptance_script(),
        "driver.py": DRIVER,
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _run(root: Path, mode: str = "sync") -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "driver.py"), str(root), mode],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(root),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# -- the floor declares it ------------------------------------------------------


def test_the_floor_declares_the_check_universal_runtime_and_enforced():
    entry = next(c for c in checks() if c["id"] == CHECK)
    assert entry["universal"] is True
    assert entry["subject"] == "runtime"
    assert entry["gate_fn"] == "check_" + CHECK
    assert CHECK in enforced_ids(None)
    assert check_ids()[-1] == "authorship_floor"


def test_the_harness_renders_the_probe():
    assert "def check_%s(http" % CHECK in render_acceptance_script()


# -- the probe ------------------------------------------------------------------


def test_a_save_that_double_checks_out_fails_the_probe(tmp_path):
    result = _run(_product(tmp_path, holds=True))
    assert result["status"] == "FAIL", result
    detail = result["detail"]
    assert "/v1/ledger_entry" in detail
    assert "N=6" in detail  # 2 x (2 + 1)
    assert "TimeoutError" in detail and "QueuePool" in detail, detail


def test_a_correct_store_passes(tmp_path):
    result = _run(_product(tmp_path, holds=False))
    assert result["status"] == "PASS", result
    assert "N=6" in result["detail"]


def test_an_async_route_does_not_hide_the_defect(tmp_path):
    # Smoke B's shape: the route serialises every request, so HTTP alone never
    # puts two writes into the store at once. The store leg still does.
    result = _run(_product(tmp_path, holds=True), mode="async")
    assert result["status"] == "FAIL", result
    assert "app.store.save('ledger_rows')" in result["detail"]
    assert "QueuePool" in result["detail"]


def test_an_async_route_over_a_correct_store_passes(tmp_path):
    result = _run(_product(tmp_path, holds=False), mode="async")
    assert result["status"] == "PASS", result


def test_success_without_distinct_stored_records_fails(tmp_path):
    result = _run(_product(tmp_path, holds=False), mode="dedupe")
    assert result["status"] == "FAIL", result
    assert "distinct stored ids" in result["detail"]


# -- N derivation ---------------------------------------------------------------


def test_n_is_twice_the_products_own_pool(tmp_path):
    result = _run(
        _product(tmp_path, holds=False, pool_size=3, max_overflow=2, threadpool=4), "width"
    )
    assert result["n"] == 10, result
    assert "pool_size=3 + max_overflow=2" in result["why"]


def test_n_is_never_below_the_declared_threadpool(tmp_path):
    result = _run(_product(tmp_path, holds=False, threadpool=25), "width")
    assert result["n"] >= 25, result
    assert result["n"] >= POOL_FACTOR * 3


def test_an_unreadable_pool_falls_back_to_the_library_default(tmp_path):
    result = _run(_product(tmp_path, holds=False, engine_raises=True), "width")
    # The loaded SQLAlchemy's own QueuePool defaults (5 + 10).
    assert result["n"] >= POOL_FACTOR * DEFAULT_POOL_CAPACITY, result
    assert "app.db.engine() raised" in result["why"]
