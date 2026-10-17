"""Master-key gated restore of accounts from disk SQLite or backup archives.

The live engine is ``ACCOUNTS_DATABASE_URL``. Leftover ``STORAGE_PATH/accounts.db``
is often probe-era only. Historical owner rows (``acct_c38ae401…``) live in
nightly ``cerebrumdev-backup-*.tar.gz`` dumps under ``STORAGE_PATH/backups``.

``cerebrum-builds`` is not the accounts store.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.accounts_backup_restore import (
    list_backup_archives,
    run_restore_from_backup,
)
from ..core.auth import require_master_key
from scripts.migrate_accounts_to_postgres import (
    AccountsRestoreError,
    compare_stores,
    run_restore,
)

router = APIRouter(prefix="/v1/ops", tags=["ops"])


def _http_error(exc: AccountsRestoreError) -> HTTPException:
    status = {
        "accounts_url_unset": 409,
        "sqlite_missing": 404,
        "target_not_empty": 409,
        "schema_missing": 409,
        "verify_failed": 500,
        "archive_missing": 404,
        "archive_unsafe": 400,
        "dump_missing": 404,
        "dump_unreadable": 409,
    }.get(exc.code, 400)
    return HTTPException(status_code=status, detail=exc.as_dict())


@router.get("/accounts-restore")
async def list_accounts_restore(_admin=Depends(require_master_key)):
    """Compare disk SQLite vs Postgres account ids and emails (no hashes)."""
    return await asyncio.to_thread(compare_stores)


@router.get("/accounts-restore/backups")
async def list_accounts_restore_backups(_admin=Depends(require_master_key)):
    """List on-disk backup archives and whether an accounts dump is present."""
    return await asyncio.to_thread(list_backup_archives)


@router.post("/accounts-restore")
async def run_accounts_restore(
    force: bool = Query(False),
    dry_run: bool = Query(False),
    _admin=Depends(require_master_key),
):
    """Copy missing SQLite accounts into ACCOUNTS_DATABASE_URL Postgres.

    Default refuses a non-empty target. ``force=true`` merges: insert missing
    ids/emails only. Re-runs are idempotent. Password hashes are copied so
    login keeps working, but they are never returned.
    """

    def _run():
        try:
            return run_restore(
                force=force,
                dry_run=dry_run,
                verify=not dry_run,
            ).as_public_dict()
        except AccountsRestoreError as exc:
            raise _http_error(exc) from exc

    return await asyncio.to_thread(_run)


@router.post("/accounts-restore/from-backup")
async def run_accounts_restore_from_backup(
    archive: str | None = Query(
        None,
        description="Backup basename, or omit for the latest archive with a dump.",
    ),
    force: bool = Query(True),
    dry_run: bool = Query(False),
    prefer_source: bool = Query(
        True,
        description=(
            "If a live account already uses a backup email under a different id, "
            "park the live email and insert the backup id/email (needed for "
            "acct_c38ae401 vs a later re-register)."
        ),
    ),
    _admin=Depends(require_master_key),
):
    """Merge accounts.dump / .sql / .db from a backup archive into Postgres.

    Default ``force=true`` and ``prefer_source=true``: insert missing ids,
    displace conflicting live emails, never wipe smoke rows. session_owners
    gaps are advisory and do not fail the request.
    """

    def _run():
        try:
            return run_restore_from_backup(
                archive=archive,
                force=force,
                dry_run=dry_run,
                prefer_source=prefer_source,
                verify=not dry_run,
            )
        except AccountsRestoreError as exc:
            raise _http_error(exc) from exc

    return await asyncio.to_thread(_run)
