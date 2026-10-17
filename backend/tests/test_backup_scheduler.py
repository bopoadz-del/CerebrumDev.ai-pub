"""The in-process backup scheduler must run, record, and survive.

New-shape tests for the 2026-08-02 finding that the render.yaml backup cron
could never execute on Render (cron jobs cannot mount disks; a disk belongs
to one service). The replacement is an asyncio task inside the web service.
What these tests pin down:

* the schedule arithmetic (next-run must never be "now" — that double-runs);
* a real backup round trip driven through ``run_backup_once`` including the
  status file and retention pruning;
* the property that a FAILED backup writes its failure to the status file
  and does not raise — a crashed scheduler loop is a silent end to all
  future backups, which is the exact failure mode this design replaces;
* the enable/disable gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

import pytest  # noqa: E402

from app.core import backup as bk  # noqa: E402
from app.core import backup_scheduler as sched  # noqa: E402

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_PW_1 = "test-" + __import__("secrets").token_hex(8)


def _seed_accounts_db(path, rows=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT)")
        for i in range(rows):
            conn.execute(
                "INSERT INTO accounts VALUES (?,?)", (f"acc_{i}", f"u{i}@example.com")
            )
        conn.commit()
    finally:
        conn.close()


class TestScheduleArithmetic:
    def test_later_today_when_hour_is_ahead(self):
        now = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
        assert sched.seconds_until_next_run(3, now) == pytest.approx(90 * 60)

    def test_tomorrow_when_hour_has_passed(self):
        now = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)
        assert sched.seconds_until_next_run(3, now) == pytest.approx(23 * 3600)

    def test_exactly_on_the_hour_schedules_tomorrow_not_now(self):
        """Zero here would run the job twice back to back: the loop calls this
        immediately after a run finishes, which can be at 03:00:00 sharp."""
        now = datetime(2026, 8, 2, 3, 0, 0, tzinfo=timezone.utc)
        assert sched.seconds_until_next_run(3, now) == pytest.approx(24 * 3600)

    def test_bad_hour_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(sched.HOUR_ENV, "banana")
        assert sched.scheduled_hour() == sched.DEFAULT_HOUR
        monkeypatch.setenv(sched.HOUR_ENV, "25")
        assert sched.scheduled_hour() == sched.DEFAULT_HOUR


class TestRunBackupOnce:
    def test_success_writes_archive_status_and_prunes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
        monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
        monkeypatch.setenv(sched.KEEP_ENV, "2")
        _seed_accounts_db(tmp_path / "storage" / "accounts.db")

        # Pre-plant stale archives so pruning has something real to do.
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
            (root / f"cerebrumdev-backup-{stamp}.tar.gz").write_bytes(b"stale")

        report = sched.run_backup_once()

        assert report["ok"] is True
        assert report["archive"] and os.path.getsize(report["archive"]) > 0
        assert "accounts.db" in report["included"]
        # keep=2: the fresh archive plus the newest stale one survive.
        remaining = sorted(p.name for p in root.glob("cerebrumdev-backup-*.tar.gz"))
        assert len(remaining) == 2
        assert report["pruned"], "retention pruning did not run"

        status = json.loads(sched.status_path().read_text(encoding="utf-8"))
        assert status["ok"] is True
        assert status["archive"] == report["archive"]
        assert sched.last_status()["ok"] is True

    def test_failure_is_recorded_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)

        def explode(**_kwargs):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(bk, "create_backup", explode)

        report = sched.run_backup_once()  # must NOT raise

        assert report["ok"] is False
        assert "disk on fire" in report["error"]
        status = json.loads(sched.status_path().read_text(encoding="utf-8"))
        assert status["ok"] is False
        assert "disk on fire" in status["error"]


class TestArming:
    def test_disabled_flag_arms_nothing(self, monkeypatch):
        monkeypatch.setenv(sched.ENABLED_ENV, "0")

        async def arm():
            return sched.start()

        assert asyncio.run(arm()) is None

    def test_enabled_creates_a_named_task(self, monkeypatch, tmp_path):
        monkeypatch.setenv(sched.ENABLED_ENV, "1")
        # Point storage somewhere empty so a bootstrap run, if it wins the
        # race before cancellation, touches nothing real.
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)

        async def arm_and_cancel():
            task = sched.start()
            assert task is not None
            assert task.get_name() == "nightly-backup"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(arm_and_cancel())

    def test_bootstrap_snapshot_taken_when_no_archive_exists(
        self, monkeypatch, tmp_path
    ):
        """A fresh deployment must not wait a day for its first protection."""
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
        monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
        _seed_accounts_db(tmp_path / "storage" / "accounts.db")
        assert sched.has_any_archive() is False

        ran = asyncio.Event()
        real_run = sched.run_backup_once

        def tracked_run():
            result = real_run()
            ran.set()
            return result

        monkeypatch.setattr(sched, "run_backup_once", tracked_run)

        async def boot():
            task = asyncio.get_running_loop().create_task(sched.scheduler_loop())
            await asyncio.wait_for(ran.wait(), timeout=30)
            task.cancel()

        asyncio.run(boot())
        assert sched.has_any_archive() is True
        assert sched.last_status()["ok"] is True


class TestEngineCutoverBackup:
    def test_host_fingerprint_is_hostname_only(self):
        url = "postgresql://user:" + _GENERATED_PW_1 + "@ep-example.c-5.us-east-2.aws.neon.tech/db?sslmode=require"
        assert bk.accounts_host_fingerprint(url) == "ep-example.c-5.us-east-2.aws.neon.tech"
        assert bk.accounts_host_fingerprint("") is None

    def test_engine_changed_when_last_backup_points_at_old_host(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        monkeypatch.setenv(
            "ACCOUNTS_DATABASE_URL",
            "postgresql://u:p@ep-new.aws.neon.tech/accounts",
        )
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        sched.status_path().write_text(
            json.dumps(
                {
                    "ok": True,
                    "at": "2026-08-16T23:49:44+00:00",
                    "engine": "postgres",
                    "accounts_host": "dpg-old.oregon-postgres.render.com",
                }
            ),
            encoding="utf-8",
        )
        assert sched.engine_changed_since_last_backup() is True

    def test_engine_unchanged_when_host_matches(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        monkeypatch.setenv(
            "ACCOUNTS_DATABASE_URL",
            "postgresql://u:p@ep-new.aws.neon.tech/accounts",
        )
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        sched.status_path().write_text(
            json.dumps(
                {
                    "ok": True,
                    "engine": "postgres",
                    "accounts_host": "ep-new.aws.neon.tech",
                }
            ),
            encoding="utf-8",
        )
        assert sched.engine_changed_since_last_backup() is False

    def test_run_backup_once_records_accounts_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
        monkeypatch.setenv(
            "ACCOUNTS_DATABASE_URL",
            "postgresql://u:p@ep-new.aws.neon.tech/accounts",
        )
        monkeypatch.setattr(
            bk,
            "create_backup",
            lambda **_kwargs: bk.BackupResult(ok=True, engine="postgres", bytes_written=1),
        )
        report = sched.run_backup_once()
        assert report["ok"] is True
        assert report["accounts_host"] == "ep-new.aws.neon.tech"
        assert sched.last_status()["accounts_host"] == "ep-new.aws.neon.tech"

    def test_scheduler_reruns_when_engine_host_changes_even_if_archives_exist(
        self, monkeypatch, tmp_path
    ):
        """A Neon cutover must not keep a pre-cutover last_backup.json forever."""
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "cerebrumdev-backup-20260816T234944Z.tar.gz").write_bytes(b"old")
        assert sched.has_any_archive() is True
        monkeypatch.setattr(sched, "engine_changed_since_last_backup", lambda: True)

        ran = asyncio.Event()

        def tracked_run():
            ran.set()
            return {"ok": True, "accounts_host": "ep-new.aws.neon.tech"}

        monkeypatch.setattr(sched, "run_backup_once", tracked_run)

        async def boot():
            task = asyncio.get_running_loop().create_task(sched.scheduler_loop())
            await asyncio.wait_for(ran.wait(), timeout=30)
            task.cancel()

        asyncio.run(boot())
        assert ran.is_set()

    def test_scheduler_retries_when_last_backup_failed_even_if_archives_exist(
        self, monkeypatch, tmp_path
    ):
        """A shipped dump fallback must not leave /ready stuck on last night's fail."""
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "cerebrumdev-backup-20260821T030000Z.tar.gz").write_bytes(b"old")
        sched.status_path().write_text(
            json.dumps(
                {
                    "ok": False,
                    "at": "2026-08-21T03:00:00.002002+00:00",
                    "error": "accounts dump failed: pg_dump failed with exit code 1",
                    "engine": "postgres",
                    "accounts_host": "ep-sweet-hill-ay5e13we.c-5.us-east-2.aws.neon.tech",
                }
            ),
            encoding="utf-8",
        )
        assert sched.has_any_archive() is True
        assert sched.last_run_failed() is True
        monkeypatch.setattr(sched, "engine_changed_since_last_backup", lambda: False)

        ran = asyncio.Event()

        def tracked_run():
            ran.set()
            return {"ok": True}

        monkeypatch.setattr(sched, "run_backup_once", tracked_run)

        async def boot():
            task = asyncio.get_running_loop().create_task(sched.scheduler_loop())
            await asyncio.wait_for(ran.wait(), timeout=30)
            task.cancel()

        asyncio.run(boot())
        assert ran.is_set()

    def test_ready_status_prefers_newer_archive_over_stale_fail(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        newer = root / "cerebrumdev-backup-20260821T091500Z.tar.gz"
        newer.write_bytes(b"success-archive")
        sched.status_path().write_text(
            json.dumps(
                {
                    "ok": False,
                    "at": "2026-08-21T03:00:00.002002+00:00",
                    "error": "accounts dump failed: pg_dump failed with exit code 1",
                    "engine": "postgres",
                    "accounts_host": "ep-example.aws.neon.tech",
                }
            ),
            encoding="utf-8",
        )
        ready = sched.last_status_for_ready()
        assert ready is not None
        assert ready["ok"] is True
        assert ready["reconciled_from"] == "newer_archive"
        assert ready["prior_error"] and "pg_dump" in ready["prior_error"]
        assert ready["archive"].endswith("cerebrumdev-backup-20260821T091500Z.tar.gz")

    def test_ready_status_keeps_fail_when_archive_is_older(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        root = bk.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "cerebrumdev-backup-20260820T030000Z.tar.gz").write_bytes(b"old-ok")
        sched.status_path().write_text(
            json.dumps(
                {
                    "ok": False,
                    "at": "2026-08-21T03:00:00.002002+00:00",
                    "error": "accounts dump failed: pg_dump failed with exit code 1",
                    "engine": "postgres",
                }
            ),
            encoding="utf-8",
        )
        ready = sched.last_status_for_ready()
        assert ready is not None
        assert ready["ok"] is False
        assert "pg_dump" in ready["error"]
