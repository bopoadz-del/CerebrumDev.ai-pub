"""Nightly backup scheduling, run inside the web service process.

Why in-process and not a Render cron job: Render cron jobs cannot mount
persistent disks, and a disk is accessible by exactly one service — so no
other service can ever see ``/app/storage``. The only process that can read
the data is this one. The web service runs continuously on a paid plan, which
makes an asyncio task a reliable scheduling vehicle.

The schedule (03:00 UTC, 14-day retention by default) and the on/off switch
are env-driven:

* ``BACKUP_SCHEDULE_ENABLED`` — "1" (default) runs the nightly job; "0"
  disables scheduling entirely (tests, local dev).
* ``BACKUP_UTC_HOUR``        — hour of day, UTC, default 3.
* ``BACKUP_KEEP``            — archives retained after pruning, default 14.

Every run — success or failure — writes ``last_backup.json`` next to the
archives, and failures log at ERROR so Sentry's logging integration exports
them. The loop itself never raises: a failed backup must not take the API
down, and a crashed API is not an acceptable way to find out a backup failed.

If no archive exists at startup, one is taken immediately rather than waiting
for the first 03:00 — a fresh deployment is unprotected until its first
snapshot, and "the cron would have run tonight" is no answer to a disk lost
this afternoon.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from . import backup

logger = logging.getLogger("cerebrumdev.backup")

ENABLED_ENV = "BACKUP_SCHEDULE_ENABLED"
HOUR_ENV = "BACKUP_UTC_HOUR"
KEEP_ENV = "BACKUP_KEEP"
DEFAULT_HOUR = 3

STATUS_FILENAME = "last_backup.json"
_ARCHIVE_STAMP_RE = re.compile(r"cerebrumdev-backup-(\d{8}T\d{6}Z)\.tar\.gz$")


def scheduler_enabled() -> bool:
    return os.getenv(ENABLED_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def scheduled_hour() -> int:
    raw = os.getenv(HOUR_ENV, "").strip()
    try:
        hour = int(raw) if raw else DEFAULT_HOUR
    except ValueError:
        logger.warning("ignoring non-numeric %s=%r", HOUR_ENV, raw)
        return DEFAULT_HOUR
    if not 0 <= hour <= 23:
        logger.warning("ignoring out-of-range %s=%r", HOUR_ENV, raw)
        return DEFAULT_HOUR
    return hour


def keep_count() -> int:
    raw = os.getenv(KEEP_ENV, "").strip()
    try:
        keep = int(raw) if raw else backup.DEFAULT_KEEP
    except ValueError:
        logger.warning("ignoring non-numeric %s=%r", KEEP_ENV, raw)
        return backup.DEFAULT_KEEP
    return keep if keep >= 1 else backup.DEFAULT_KEEP


def seconds_until_next_run(hour: int, now: Optional[datetime] = None) -> float:
    """Seconds from ``now`` until the next occurrence of ``hour``:00 UTC.

    A run scheduled for exactly ``now`` counts as the NEXT day's run — the
    caller invokes this right after finishing a backup, and returning 0 there
    would run the job twice back to back.
    """
    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def status_path() -> Path:
    return backup.backup_root() / STATUS_FILENAME


def last_status() -> Optional[Dict[str, Any]]:
    """The most recent run's report, or None if no run is on record."""
    path = status_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def has_any_archive() -> bool:
    root = backup.backup_root()
    if not root.is_dir():
        return False
    return any(root.glob("cerebrumdev-backup-*.tar.gz"))


def newest_archive() -> Optional[Path]:
    root = backup.backup_root()
    if not root.is_dir():
        return None
    archives = sorted(
        (p for p in root.glob("cerebrumdev-backup-*.tar.gz") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    return archives[0] if archives else None


def last_run_failed() -> bool:
    last = last_status() or {}
    return last.get("ok") is False


def _archive_stamp(path: Path) -> Optional[datetime]:
    match = _ARCHIVE_STAMP_RE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _status_at(last: Dict[str, Any]) -> Optional[datetime]:
    raw = last.get("at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def last_status_for_ready() -> Optional[Dict[str, Any]]:
    """Status for /ready. A newer successful archive beats a stale fail record.

    Shipping fallback code does not rewrite last_backup.json. If a later
    archive exists on disk, reporting the old pg_dump fail would be a lie.
    """
    last = last_status()
    newest = newest_archive()
    if last is None:
        return None
    if last.get("ok"):
        return last
    if newest is None:
        return last
    arch_at = _archive_stamp(newest)
    fail_at = _status_at(last)
    if arch_at is None or fail_at is None or arch_at <= fail_at:
        return last
    return {
        **last,
        "ok": True,
        "error": None,
        "archive": str(newest),
        "bytes_written": newest.stat().st_size,
        "reconciled_from": "newer_archive",
        "prior_error": last.get("error"),
        "at": arch_at.isoformat(),
    }


def engine_changed_since_last_backup() -> bool:
    """True when live ACCOUNTS_DATABASE_URL host != last_backup.json host.

    Pre-cutover status files omit accounts_host, so a Neon switch is detected
    even when archives already exist on disk.
    """
    current = backup.accounts_host_fingerprint()
    if not current:
        return False
    last = last_status() or {}
    return last.get("accounts_host") != current


def run_backup_once() -> Dict[str, Any]:
    """One backup + prune. Blocking; never raises.

    The report — written to ``last_backup.json`` and returned — is the record
    of what happened, including failure. Raising instead would kill the
    scheduler loop and silently end all future backups.
    """
    started = datetime.now(timezone.utc).isoformat()
    host = backup.accounts_host_fingerprint()
    try:
        result = backup.create_backup()
        report: Dict[str, Any] = {
            "at": started,
            **result.to_dict(),
            "accounts_host": host,
        }
        if result.ok:
            removed = backup.prune_backups(keep=keep_count())
            report["pruned"] = [p.name for p in removed]
            try:
                from app.factory.paths import cleanup_stale_session_outputs

                report["factory_outputs_cleanup"] = cleanup_stale_session_outputs()
            except Exception as exc:  # noqa: BLE001 — cleanup must not fail the backup
                logger.warning("factory_outputs cleanup skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001 — the loop must survive anything
        report = {
            "at": started,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "accounts_host": host,
        }

    try:
        path = status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("could not write backup status file: %s", exc)

    if report.get("ok"):
        logger.info(
            "nightly backup ok: %s (%d bytes, included=%s)",
            report.get("archive"),
            report.get("bytes_written", 0),
            report.get("included"),
        )
    else:
        # ERROR on purpose: Sentry's logging integration turns this into an
        # event, which is the only alerting channel an in-process job has.
        logger.error("nightly backup FAILED: %s", report.get("error"))
    return report


async def scheduler_loop() -> None:
    if not has_any_archive():
        logger.info("no existing backup archive — taking a bootstrap snapshot now")
        await asyncio.to_thread(run_backup_once)
    elif engine_changed_since_last_backup():
        logger.info(
            "accounts engine host changed since last_backup.json — taking a cutover snapshot now"
        )
        await asyncio.to_thread(run_backup_once)
    elif last_run_failed():
        logger.info(
            "last backup failed — retrying now so /ready is not stuck on a stale fail"
        )
        await asyncio.to_thread(run_backup_once)
    while True:
        delay = seconds_until_next_run(scheduled_hour())
        logger.info("backup scheduler: next run in %.0fs", delay)
        await asyncio.sleep(delay)
        await asyncio.to_thread(run_backup_once)


def start() -> Optional["asyncio.Task[None]"]:
    """Arm the scheduler. Returns the task, or None when disabled."""
    if not scheduler_enabled():
        logger.info("backup scheduler disabled via %s", ENABLED_ENV)
        return None
    return asyncio.get_running_loop().create_task(scheduler_loop(), name="nightly-backup")
