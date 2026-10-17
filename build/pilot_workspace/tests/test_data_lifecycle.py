"""S10 data lifecycle — performed, not configured.

Schema up/down on a populated v1 DB, a restore drill (backup → wipe →
restore → assert rows), and parallel writes at the FastAPI sync threadpool
size. connect() must not CREATE TABLE.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from app import backup, store
from app.migrations import current_revision, downgrade, upgrade_head, upgrade_to

ENTITY = 'analytics_surface'
SAMPLE = {'reference': 's10-row', 'status': 'open', 'quantity': 0}
ENTITIES = ['analytics_surface', 'dashboard_surface']
REV_V1 = '0001_baseline'
REV_V2 = '0002_lifecycle_audit'
AUDIT = 'lifecycle_audit'


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path


def _tables() -> set[str]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_store_source_has_no_create_table_if_not_exists():
    src = Path(__file__).resolve().parents[1] / "app" / "store.py"
    text = src.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in text
    assert "PRAGMA journal_mode=WAL" in text
    assert "busy_timeout" in text


def test_connect_does_not_create_domain_tables(isolated_db):
    conn = store.connect()
    try:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    for name in ENTITIES:
        assert name not in names, name


@pytest.mark.skipif(not ENTITY, reason="no domain entity to migrate")
def test_schema_change_applies_to_populated_v1_and_rolls_back(isolated_db):
    assert upgrade_to(REV_V1) == REV_V1
    saved = store.save(ENTITY, dict(SAMPLE))
    assert saved["id"] is not None
    assert store.get(ENTITY, saved["id"]) is not None
    assert ENTITY in _tables()
    assert AUDIT not in _tables()

    assert upgrade_head() == REV_V2
    fetched = store.get(ENTITY, saved["id"])
    assert fetched is not None, "v1 row did not survive upgrade to v2"
    for key, value in SAMPLE.items():
        assert fetched[key] == value
    assert AUDIT in _tables()
    assert current_revision() == REV_V2

    assert downgrade(REV_V1) == REV_V1
    rolled = store.get(ENTITY, saved["id"])
    assert rolled is not None, "v1 row did not survive downgrade"
    for key, value in SAMPLE.items():
        assert rolled[key] == value
    assert AUDIT not in _tables()
    assert current_revision() == REV_V1


@pytest.mark.skipif(not ENTITY, reason="no domain entity to restore")
def test_restore_drill_backup_wipe_restore_rows(isolated_db):
    upgrade_head()
    original = [store.save(ENTITY, dict(SAMPLE)) for _ in range(3)]
    ids = [row["id"] for row in original]
    archive = backup.create_backup()
    assert archive.is_file() and archive.stat().st_size > 0

    backup.wipe_database()
    assert not store.db_path().exists()

    backup.restore_backup(archive)
    assert store.db_path().exists()
    restored_ids = [row["id"] for row in store.list_all(ENTITY)]
    assert restored_ids == ids
    for row in original:
        fetched = store.get(ENTITY, row["id"])
        assert fetched is not None
        for key, value in SAMPLE.items():
            assert fetched[key] == value


@pytest.mark.skipif(not ENTITY, reason="no domain entity to write")
def test_parallel_writes_match_fastapi_threadpool(isolated_db):
    upgrade_head()
    workers = store.FASTAPI_SYNC_THREADPOOL

    def _write(i: int):
        payload = dict(SAMPLE)
        if "reference" in payload:
            payload["reference"] = f"s10-{i}"
        return store.save(ENTITY, payload)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_write, i) for i in range(workers)]
        results = [f.result() for f in as_completed(futures)]
    assert len(results) == workers
    assert all(r["id"] is not None for r in results)
    persisted = store.list_all(ENTITY)
    assert len(persisted) == workers
    assert {r["id"] for r in persisted} == {r["id"] for r in results}


def test_wal_and_busy_timeout_after_migrate(isolated_db):
    upgrade_head()
    conn = store.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"
    assert int(timeout) >= store.SQLITE_BUSY_TIMEOUT_MS


def test_retention_prunes_old_backups(isolated_db):
    upgrade_head()
    store.save(ENTITY, dict(SAMPLE)) if ENTITY else None
    root = backup.backup_root()
    root.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (root / f"platform-2026080{i}T000000Z.db").write_bytes(b"stub")
    removed = backup.prune_backups(keep=3)
    remaining = sorted(p.name for p in backup.list_backups() if p.stat().st_size == 4)
    assert len(removed) == 3
    assert remaining == [
        "platform-20260803T000000Z.db",
        "platform-20260804T000000Z.db",
        "platform-20260805T000000Z.db",
    ]
