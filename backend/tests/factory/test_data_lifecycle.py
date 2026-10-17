"""S10: a generated product writes real data, with versioned migrations.

The restore drill is PERFORMED here (backup file → wipe → restore → assert
rows), not merely configured. Schema v1→v2 on a populated DB rolls back.
Parallel writes run at the FastAPI sync threadpool size.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.data_lifecycle import (
    AUDIT_TABLE,
    FASTAPI_SYNC_THREADPOOL,
    REVISION_0001,
    REVISION_0002,
    assert_no_connect_time_ddl,
    migration_table_names,
)
from app.factory.build.runner import RoleRunner
from tests.factory.conftest import stub_coder_patches

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    # Module-scoped: autouse monkeypatch has not run yet.
    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path_factory.mktemp("s10") / "build"
    # 0.5: without a coding agent the WRITER refuses (writer_no_output);
    # stub a deterministic agent so the S10 build still goes green.
    with stub_coder_patches():
        outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    return out


def _probe(built: Path, storage: Path, body: str) -> dict:
    """Run *body* inside the generated product, not the factory ``app`` package."""
    script = (
        "import json, os, sys\n"
        f"os.environ['STORAGE_PATH'] = {str(storage / 'data')!r}\n"
        f"os.environ['BACKUP_DIR'] = {str(storage / 'backups')!r}\n"
        f"sys.path.insert(0, {str(built)!r})\n"
        + body
        + "\nprint('S10_PROBE=' + json.dumps(result))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=built,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("S10_PROBE=")]
    assert line, proc.stdout + proc.stderr
    return json.loads(line[-1].split("=", 1)[1])


def test_emitted_store_has_no_create_table_if_not_exists(built):
    store_src = (built / "app" / "store.py").read_text(encoding="utf-8")
    assert_no_connect_time_ddl(store_src)
    assert "PRAGMA journal_mode=WAL" in store_src
    assert "busy_timeout" in store_src
    reqs = (built / "requirements.txt").read_text(encoding="utf-8")
    assert "alembic" in reqs
    assert "sqlalchemy" in reqs
    mig = (built / "alembic" / "versions" / "0001_baseline.py").read_text(
        encoding="utf-8"
    )
    tables = migration_table_names(mig)
    assert "analytics_surface" in tables
    assert "dashboard_surface" in tables
    v2 = (built / "alembic" / "versions" / "0002_lifecycle_audit.py").read_text(
        encoding="utf-8"
    )
    assert "def upgrade" in v2 and "def downgrade" in v2
    assert AUDIT_TABLE in v2
    entry = (built / "scripts" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in entry
    main = (built / "app" / "main.py").read_text(encoding="utf-8")
    assert "upgrade_head" in main
    assert "lifespan" in main
    doc = json.loads((built / "docs" / "data_lifecycle.json").read_text(encoding="utf-8"))
    assert doc["sqlite_on_mounted_disk"] is True
    assert "SPOF" in doc["spof"] or "spof" in doc["spof"].lower()
    assert doc["capacity"]["disk_gb"] == 1
    assert doc["capacity"]["ha"] is False


def test_schema_change_on_populated_v1_is_performed_and_rolls_back(built, tmp_path):
    result = _probe(
        built,
        tmp_path,
        "\n".join(
            [
                "from app import store",
                "from app.migrations import current_revision, downgrade, upgrade_head, upgrade_to",
                f"v1 = upgrade_to({REVISION_0001!r})",
                "row = store.save('analytics_surface', "
                "{'reference': 's10-v1', 'status': 'open', 'quantity': 2}, tenant_id='local')",
                "conn = store.connect()",
                "tables_v1 = {r[0] for r in conn.execute("
                "\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}",
                "conn.close()",
                "v2 = upgrade_head()",
                "after = store.get('analytics_surface', row['id'], tenant_id='local')",
                "conn = store.connect()",
                "tables_v2 = {r[0] for r in conn.execute("
                "\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}",
                "conn.close()",
                f"back = downgrade({REVISION_0001!r})",
                "rolled = store.get('analytics_surface', row['id'], tenant_id='local')",
                "conn = store.connect()",
                "tables_down = {r[0] for r in conn.execute("
                "\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}",
                "conn.close()",
                "result = {",
                "    'v1': v1, 'v2': v2, 'back': back,",
                "    'row_id': row['id'],",
                "    'survived_upgrade': after is not None and after.get('reference') == 's10-v1',",
                "    'survived_downgrade': rolled is not None and rolled.get('reference') == 's10-v1',",
                f"    'audit_after_upgrade': {AUDIT_TABLE!r} in tables_v2,",
                f"    'audit_after_downgrade': {AUDIT_TABLE!r} in tables_down,",
                "    'entity_after_downgrade': 'analytics_surface' in tables_down,",
                "    'current_after_downgrade': current_revision(),",
                "}",
            ]
        ),
    )
    assert result["v1"] == REVISION_0001
    assert result["v2"] == REVISION_0002
    assert result["back"] == REVISION_0001
    assert result["survived_upgrade"] is True
    assert result["survived_downgrade"] is True
    assert result["audit_after_upgrade"] is True
    assert result["audit_after_downgrade"] is False
    assert result["entity_after_downgrade"] is True
    assert result["current_after_downgrade"] == REVISION_0001


def test_restore_drill_is_performed(built, tmp_path):
    result = _probe(
        built,
        tmp_path,
        "\n".join(
            [
                "from pathlib import Path",
                "from app import backup, store",
                "from app.migrations import upgrade_head",
                "upgrade_head()",
                "rows = [store.save('analytics_surface', "
                "{'reference': f'keep-{i}', 'status': 'open', 'quantity': i}, tenant_id='local') "
                "for i in range(5)]",
                "ids = [r['id'] for r in rows]",
                "archive = backup.create_backup()",
                "size = archive.stat().st_size",
                "backup.wipe_database()",
                "wiped = not Path(store.db_path()).exists()",
                "backup.restore_backup(archive)",
                "restored = [r['id'] for r in store.list_all('analytics_surface', tenant_id='local')]",
                "refs = [r['reference'] for r in store.list_all('analytics_surface', tenant_id='local')]",
                "result = {",
                "    'ids': ids,",
                "    'restored': restored,",
                "    'refs': refs,",
                "    'archive': archive.name,",
                "    'archive_bytes': size,",
                "    'wiped': wiped,",
                "    'performed': True,",
                "}",
            ]
        ),
    )
    assert result["performed"] is True
    assert result["wiped"] is True
    assert result["archive_bytes"] > 0
    assert result["restored"] == result["ids"]
    assert result["refs"] == [f"keep-{i}" for i in range(5)]


def test_parallel_writes_at_fastapi_threadpool_are_performed(built, tmp_path):
    result = _probe(
        built,
        tmp_path,
        "\n".join(
            [
                "from concurrent.futures import ThreadPoolExecutor, as_completed",
                "from app import store",
                "from app.migrations import upgrade_head",
                "upgrade_head()",
                "workers = store.FASTAPI_SYNC_THREADPOOL",
                "def _write(i):",
                "    return store.save('analytics_surface', "
                "{'reference': f'p-{i}', 'status': 'open', 'quantity': i}, tenant_id='local')",
                "with ThreadPoolExecutor(max_workers=workers) as pool:",
                "    futs = [pool.submit(_write, i) for i in range(workers)]",
                "    written = [f.result() for f in as_completed(futs)]",
                "persisted = store.list_all('analytics_surface', tenant_id='local')",
                "conn = store.connect()",
                "mode = conn.execute('PRAGMA journal_mode').fetchone()[0]",
                "timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]",
                "conn.close()",
                "result = {",
                "    'workers': workers,",
                "    'written': len(written),",
                "    'persisted': len(persisted),",
                "    'ids_match': {r['id'] for r in persisted} == {r['id'] for r in written},",
                "    'wal': str(mode).lower(),",
                "    'busy_timeout': int(timeout),",
                "}",
            ]
        ),
    )
    assert result["workers"] == FASTAPI_SYNC_THREADPOOL
    assert result["written"] == FASTAPI_SYNC_THREADPOOL
    assert result["persisted"] == FASTAPI_SYNC_THREADPOOL
    assert result["ids_match"] is True
    assert result["wal"] == "wal"
    assert result["busy_timeout"] >= 30_000


def test_connect_without_migrate_has_no_domain_tables(built, tmp_path):
    result = _probe(
        built,
        tmp_path,
        "\n".join(
            [
                "from app import store",
                "conn = store.connect()",
                "names = {r[0] for r in conn.execute("
                "\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()}",
                "conn.close()",
                "result = {'names': sorted(names), "
                "'has_analytics': 'analytics_surface' in names}",
            ]
        ),
    )
    assert result["has_analytics"] is False


def test_appointment_revision_emits_valid_sqlite_ddl_with_id_primary_key():
    """Live veterinary-care shape: scheduled_time TEXT, duration_minutes INTEGER.

    The invalid form (PRIMARY KEY (id) with no id column) is what sqlite
    rejects. The emitter must include id and still type the appointment
    columns so a new-domain workspace can migrate.
    """
    import sqlite3

    from app.factory.build.data_lifecycle import render_revision_0001, render_store

    specs = {
        "end_to_end_appointment_workflow": {
            "entity": "appointment",
            "fields": [
                {"name": "scheduled_time", "type": "str"},
                {"name": "duration_minutes", "type": "int"},
                {"name": "status", "type": "str"},
                {"name": "service_type", "type": "str"},
            ],
        }
    }
    rev = render_revision_0001(specs)
    assert 'sa.Column("id", sa.Integer(), primary_key=True' in rev
    assert 'sa.Column("scheduled_time", sa.Text()' in rev
    assert 'sa.Column("duration_minutes", sa.Integer()' in rev
    assert 'sa.Column("status", sa.Text()' in rev
    assert 'sa.Column("service_type", sa.Text()' in rev
    store_src = render_store(specs)
    assert_no_connect_time_ddl(store_src)
    assert "appointment" in store_src

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE appointment ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "scheduled_time TEXT, "
        "duration_minutes INTEGER, "
        "status TEXT, "
        "service_type TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO appointment "
        "(scheduled_time, duration_minutes, status, service_type) "
        "VALUES ('10:00:00', 30, 'booked', 'consult')"
    )
    row = conn.execute("SELECT * FROM appointment").fetchone()
    assert row[1] == "10:00:00"
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "CREATE TABLE broken_appointment ("
            "scheduled_time TEXT, "
            "duration_minutes INTEGER, "
            "status TEXT, "
            "service_type TEXT, "
            "PRIMARY KEY (id)"
            ")"
        )
    conn.close()
