"""Backup tests shaped as conservation checks.

The failure this guards against is not "the backup job errored" -- that is
loud and someone notices. It is "the backup job succeeded every night for a
year and the archive turns out to be unrestorable". So these tests always go
all the way round: write real rows, snapshot, restore into a clean location,
and compare what came back against what went in.
"""

from __future__ import annotations

import os
import json
import sqlite3
import tarfile

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

import pytest  # noqa: E402

from app.core import backup as bk  # noqa: E402


def _seed_accounts_db(path, rows=5):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT, pw_hash TEXT)"
        )
        conn.execute("CREATE TABLE api_keys (id TEXT PRIMARY KEY, account_id TEXT)")
        for i in range(rows):
            conn.execute(
                "INSERT INTO accounts VALUES (?,?,?)",
                (f"acc_{i}", f"user{i}@example.com", f"pbkdf2$hash{i}"),
            )
            conn.execute("INSERT INTO api_keys VALUES (?,?)", (f"key_{i}", f"acc_{i}"))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(root))
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return root


class TestRoundTrip:
    def test_every_row_survives_backup_and_restore(self, storage, tmp_path):
        """The conservation property: nothing is lost in the round trip."""
        _seed_accounts_db(storage / "accounts.db", rows=7)

        result = bk.create_backup()
        assert result.ok, result.error
        assert result.archive is not None and result.archive.is_file()

        restored = bk.restore_backup(result.archive, tmp_path / "restored")
        counts = restored["verified"]

        assert counts["accounts"] == 7
        assert counts["api_keys"] == 7

        conn = sqlite3.connect(str(tmp_path / "restored" / "accounts.db"))
        try:
            emails = {r[0] for r in conn.execute("SELECT email FROM accounts")}
        finally:
            conn.close()
        assert emails == {f"user{i}@example.com" for i in range(7)}

    def test_backup_captures_content_directories(self, storage, tmp_path):
        _seed_accounts_db(storage / "accounts.db", rows=1)
        (storage / "uploads").mkdir()
        (storage / "uploads" / "a.txt").write_text("payload", encoding="utf-8")

        result = bk.create_backup()
        assert result.ok
        assert "uploads" in result.included

        bk.restore_backup(result.archive, tmp_path / "restored")
        assert (tmp_path / "restored" / "uploads" / "a.txt").read_text(
            encoding="utf-8"
        ) == "payload"

    def test_absent_content_is_reported_not_silently_dropped(self, storage):
        _seed_accounts_db(storage / "accounts.db", rows=1)
        result = bk.create_backup()
        assert result.ok
        assert set(result.skipped) >= {"uploads", "sessions", "chroma"}


class TestCorruptionIsCaught:
    def test_snapshot_of_a_live_database_is_consistent(self, storage, tmp_path):
        """A writer mid-transaction must not produce a torn snapshot."""
        db = storage / "accounts.db"
        _seed_accounts_db(db, rows=3)

        writer = sqlite3.connect(str(db))
        try:
            writer.execute("BEGIN")
            writer.execute(
                "INSERT INTO accounts VALUES ('acc_99','pending@example.com','x')"
            )
            dest = tmp_path / "snap.db"
            bk.snapshot_sqlite(db, dest)
            counts = bk.verify_sqlite_snapshot(dest)
        finally:
            writer.rollback()
            writer.close()

        assert counts["accounts"] == 3, "snapshot captured an uncommitted write"

    def test_verify_rejects_a_corrupt_snapshot(self, tmp_path):
        junk = tmp_path / "corrupt.db"
        junk.write_bytes(b"this is definitely not a sqlite database")
        with pytest.raises(Exception):
            bk.verify_sqlite_snapshot(junk)


class TestArchiveSafety:
    def test_restore_refuses_path_traversal(self, tmp_path):
        """A crafted archive must not write outside the target."""
        evil = tmp_path / "evil.tar.gz"
        payload = tmp_path / "payload.txt"
        payload.write_text("owned", encoding="utf-8")
        with tarfile.open(evil, "w:gz") as tar:
            tar.add(payload, arcname="../escaped.txt")

        with pytest.raises(RuntimeError, match="unsafe archive member"):
            bk.restore_backup(evil, tmp_path / "target")
        assert not (tmp_path / "escaped.txt").exists()


class TestRetention:
    def test_prune_keeps_the_newest_and_removes_the_rest(self, storage, tmp_path):
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        names = [f"cerebrumdev-backup-2026080{i}T000000Z.tar.gz" for i in range(1, 8)]
        for n in names:
            (root / n).write_bytes(b"x")

        removed = bk.prune_backups(keep=3)

        remaining = sorted(p.name for p in root.glob("cerebrumdev-backup-*.tar.gz"))
        assert len(remaining) == 3
        assert remaining == sorted(names[-3:])
        assert len(removed) == 4

    def test_prune_on_an_empty_root_is_a_noop(self, storage):
        assert bk.prune_backups(keep=3) == []


class TestPostgresDumpHonesty:
    def test_postgres_dump_fails_honestly_without_pg_dump(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bk.shutil, "which", lambda _name: None)

        def boom(*_a, **_k):
            raise RuntimeError("could not connect")

        monkeypatch.setattr(bk, "snapshot_postgres_via_sqlalchemy", boom)
        with pytest.raises(RuntimeError, match="pg_dump is not installed"):
            bk.snapshot_postgres("postgresql://example", tmp_path / "x.dump")

    def test_sqlalchemy_fallback_when_pg_dump_exits_nonzero(self, tmp_path, monkeypatch):
        """Neon PG18 + slim-image pg_dump 15/16 exits 1 (version mismatch)."""
        db = tmp_path / "acc.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE accounts (id TEXT, email TEXT)")
            conn.execute("INSERT INTO accounts VALUES ('a1', 'a@b.com')")
            conn.commit()
        finally:
            conn.close()

        monkeypatch.setattr(bk.shutil, "which", lambda _name: "/usr/bin/pg_dump")

        class _Proc:
            returncode = 1
            stderr = "pg_dump: error: aborting because of server version mismatch"
            stdout = ""

        monkeypatch.setattr(bk.subprocess, "run", lambda *_a, **_k: _Proc())
        dest = tmp_path / "accounts.dump"
        written = bk.snapshot_postgres(f"sqlite:///{db}", dest)
        # The fallback is the structured snapshot: typed rows, no SQL text.
        assert written.name == bk.ACCOUNTS_SNAPSHOT_NAME
        doc = json.loads(written.read_text(encoding="utf-8"))
        assert doc["schema"] == bk.ACCOUNTS_SNAPSHOT_SCHEMA
        assert doc["tables"]["accounts"] == {"columns": ["id", "email"], "rows": [["a1", "a@b.com"]]}
        assert dest.exists() is False or dest.stat().st_size == 0

    def test_sqlalchemy_fallback_is_used_by_create_backup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
        db = tmp_path / "acc.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE accounts (id TEXT, email TEXT)")
            conn.execute("INSERT INTO accounts VALUES ('a1', 'ops@example.com')")
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{db}")
        monkeypatch.setattr(bk.shutil, "which", lambda _name: "/usr/bin/pg_dump")

        class _Proc:
            returncode = 1
            stderr = "pg_dump: error: aborting because of server version mismatch"
            stdout = ""

        monkeypatch.setattr(bk.subprocess, "run", lambda *_a, **_k: _Proc())
        result = bk.create_backup(include_content=False)
        assert result.ok is True
        assert result.dump_method == "structured"
        assert bk.ACCOUNTS_SNAPSHOT_NAME in result.included
        assert result.archive is not None and result.archive.is_file()
