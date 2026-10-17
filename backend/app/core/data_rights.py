"""Data rights: erasure and export across every store that holds user data.

The accounts database is only one of the places a user's data lives. Session
state, uploaded files, extracted text, vector embeddings and Google Drive OAuth
tokens all sit on disk or in Chroma, keyed by ``session_id`` — and the only
index from an account to its sessions is ``accounts_store.session_owners``.
This module is the single place that walks that index and takes everything
down in a safe order.

Design rules, each of which exists because the obvious implementation is wrong:

* **Vectors before files.** ``session_store.get_session`` falls back to
  ``_rehydrate_from_chroma`` when the disk snapshot is missing. Deleting the
  session directory while leaving the Chroma collection standing does not erase
  the session — it *resurrects* it on the next read, re-attributed to
  ``user_id="anonymous"``. A failed vector purge is a resurrection path, not a
  cosmetic leftover.
* **No ``ignore_errors=True``.** A silent ``rmtree`` that swallows a Windows
  file-handle error and returns cleanly is exactly the partial purge that
  claims success. Every failure is caught per session and named in the report.
* **"Cannot check" is not "clean".** ``chroma_store.collection_exists``
  returns False on any exception, so a host without ``chromadb`` installed
  would otherwise report a spotless vector purge. Absent, purged and
  unavailable are three different statuses here.
* **Never widen beyond the ownership index.** Nothing globs the sessions
  directory. A session whose ownership row was never written is unreachable,
  and is reported as such rather than guessed at.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from . import accounts_store

logger = logging.getLogger(__name__)

# Status vocabulary for a purge category.
PURGED = "purged"          # data existed and is gone
ABSENT = "absent"          # nothing was there to remove
PARTIAL = "partial"        # some removed, some failed
FAILED = "failed"          # nothing could be removed
UNAVAILABLE = "unavailable"  # could not even determine what is there

_CLEAN_STATUSES = {PURGED, ABSENT}


def _storage_roots() -> List[Path]:
    """Every distinct storage root a session may have been written under.

    ``session_persistence`` and ``upload_processor`` snapshot ``STORAGE_PATH``
    at import time, while ``google_drive_connector`` reads it per call. When a
    process (or a test) changes the variable after import those disagree, so a
    purge that trusts a single root can miss a whole tree. Resolve both and
    de-duplicate.
    """
    candidates: List[str] = []
    try:
        from . import session_persistence

        configured = getattr(session_persistence, "STORAGE_PATH", None)
        if configured:
            candidates.append(str(configured))
    except Exception as exc:  # noqa: BLE001 — never let a purge die on an import
        logger.warning("Could not read session_persistence.STORAGE_PATH: %s", exc)
    candidates.append(os.getenv("STORAGE_PATH", "./storage"))

    roots: List[Path] = []
    seen = set()
    for raw in candidates:
        try:
            resolved = Path(raw).resolve()
        except Exception:  # noqa: BLE001
            continue
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _partition_session_ids(session_ids: List[str]) -> tuple[List[str], List[str]]:
    """Split session ids into ones safe to use as a path segment and ones not.

    ``record_session_owner`` stores whatever string its caller passed, and these
    ids are about to be interpolated into a recursive delete. An id of
    ``../../..`` would make this function a remote directory-removal primitive
    aimed at whatever the storage root's parent happens to be. Ids that cannot
    be a single path segment are never turned into a path; they are reported
    instead, because refusing to delete is recoverable and deleting the wrong
    tree is not.
    """
    safe: List[str] = []
    unsafe: List[str] = []
    for sid in session_ids:
        candidate = (sid or "").strip()
        if (
            not candidate
            or candidate in {".", ".."}
            or "/" in candidate
            or "\\" in candidate
            or "\x00" in candidate
            or Path(candidate).is_absolute()
            or len(Path(candidate).parts) != 1
        ):
            unsafe.append(sid)
        else:
            safe.append(candidate)
    return safe, unsafe


def _purge_trees(paths: List[Path], roots: List[Path]) -> Dict[str, Any]:
    """Remove directory trees, reporting per-path failures honestly.

    Second line of defence behind ``_partition_session_ids``: every target is
    re-checked for containment inside a known storage root after resolution, so
    a symlink or an id that slipped through cannot redirect the delete.
    """
    removed: List[str] = []
    errors: List[str] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            resolved = path.resolve()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: unresolvable ({exc})")
            continue
        if not any(resolved.is_relative_to(root) for root in roots):
            errors.append(f"{path}: resolves outside the storage root; refused")
            logger.error("Refusing to purge %s: outside storage roots", resolved)
            continue
        try:
            shutil.rmtree(resolved)  # deliberately not ignore_errors
            removed.append(str(resolved))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: {exc}")
            logger.warning("Failed to remove %s: %s", path, exc)
    if errors:
        return {
            "status": PARTIAL if removed else FAILED,
            "removed": removed,
            "errors": errors,
        }
    return {
        "status": PURGED if removed else ABSENT,
        "removed": removed,
        "errors": [],
    }


def _purge_vector_index(session_ids: List[str]) -> Dict[str, Any]:
    """Delete each session's Chroma collection.

    Returns ``unavailable`` — never ``absent`` — when Chroma itself cannot be
    reached, because "we could not look" must not be recorded as "there was
    nothing there".
    """
    if not session_ids:
        return {"status": ABSENT, "deleted": [], "errors": []}

    from . import chroma_store

    try:
        client = chroma_store._get_chroma_client()
        existing = {
            c.name if hasattr(c, "name") else str(c) for c in client.list_collections()
        }
    except Exception as exc:  # noqa: BLE001 — chromadb may not be installed
        return {
            "status": UNAVAILABLE,
            "deleted": [],
            "errors": [f"chroma unreachable: {exc}"],
            "detail": (
                "Vector entries could not be inspected or removed. Until this "
                "is retried the embeddings survive and can rehydrate the "
                "session."
            ),
        }

    deleted: List[str] = []
    errors: List[str] = []
    for session_id in session_ids:
        name = chroma_store.collection_name(session_id)
        if name not in existing:
            continue
        try:
            client.delete_collection(name=name)
            deleted.append(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            logger.warning("Failed to delete Chroma collection %s: %s", name, exc)
    if errors:
        return {
            "status": PARTIAL if deleted else FAILED,
            "deleted": deleted,
            "errors": errors,
        }
    return {
        "status": PURGED if deleted else ABSENT,
        "deleted": deleted,
        "errors": [],
    }


def _evict_in_memory(session_ids: List[str]) -> Dict[str, Any]:
    """Drop cached SessionState objects so a purged session cannot be served."""
    try:
        from . import session_store
    except Exception as exc:  # noqa: BLE001
        return {"status": UNAVAILABLE, "evicted": [], "errors": [str(exc)]}
    evicted = [
        sid for sid in session_ids if session_store._session_store.pop(sid, None) is not None
    ]
    return {"status": PURGED if evicted else ABSENT, "evicted": evicted, "errors": []}


def _structural_residue() -> List[Dict[str, str]]:
    """Data that is genuinely not reachable from an account id.

    Listed on every purge so the limitation is visible in the result rather
    than discovered later. None of these are attributable to a single account
    without a scan we are not willing to perform blind.
    """
    return [
        {
            "category": "change_request_workspaces",
            "reason": (
                "STORAGE_PATH/change_requests is keyed by request_id and "
                "product_id, with no account or session index to join on."
            ),
        },
        {
            "category": "grounding_audit_log",
            "reason": (
                "STORAGE_PATH/grounding/verdicts.jsonl is a shared append-only "
                "audit log; entries are not account-keyed and rewriting it "
                "during a purge would corrupt concurrent writers."
            ),
        },
        {
            "category": "orphan_sessions",
            "reason": (
                "session_store.create_session records ownership on a "
                "best-effort basis (failures are logged, not raised). A "
                "session whose ownership row was never written is invisible to "
                "sessions_for_owner and survives this purge."
            ),
        },
        {
            "category": "other_workers_session_cache",
            "reason": (
                "session_store._session_store is per-process. This purge evicts "
                "the cache of the worker that served the request; sibling "
                "workers drop theirs on restart, and cannot serve the session "
                "onward because its snapshot and vectors are gone."
            ),
        },
        {
            "category": "external_processors",
            "reason": (
                "Stripe customer and subscription records, and any mail "
                "provider logs, live with those processors and must be deleted "
                "through their own APIs."
            ),
        },
    ]


def purge_session(session_id: str) -> Dict[str, Any]:
    """Erase one session: what the owner's Delete in the session list does.

    The same steps, helpers and order as :func:`purge_account`, for a single
    id -- vectors first (the resurrection path), then the on-disk trees, then
    the in-process cache, and the ownership row LAST, so a partial failure
    leaves the session still listed and deletable again rather than orphaned.
    The generated platform's workspace under ``factory_outputs`` goes too:
    a deleted session must not keep eating the persistent disk.

    Returns a report; never raises for a partial purge.
    """
    safe_ids, unsafe_ids = _partition_session_ids([session_id])
    roots = _storage_roots()
    categories: Dict[str, Any] = {}
    categories["vector_index"] = _purge_vector_index([session_id])

    paths: List[Path] = []
    for sid in safe_ids:
        for root in roots:
            paths += [
                root / "sessions" / sid,
                root / "google_drive" / sid,
                root / "workbench" / "sessions" / sid,
            ]
    output_roots = list(roots)
    try:
        from app.factory.paths import factory_outputs_root

        outputs = factory_outputs_root().resolve()
        output_roots.append(outputs)
        paths += [outputs / "sessions" / sid for sid in safe_ids]
    except Exception as exc:  # noqa: BLE001 -- never let a delete die on an import
        logger.warning("purge_session: factory outputs root unavailable: %s", exc)
    categories["session_storage"] = _purge_trees(paths, output_roots)
    categories["session_cache"] = _evict_in_memory([session_id])
    if unsafe_ids:
        categories["unsafe_session_ids"] = {
            "status": FAILED,
            "errors": [f"{session_id!r}: not a single path segment; on-disk content not purged"],
        }

    content_ok = all(info["status"] in _CLEAN_STATUSES for info in categories.values())
    if content_ok:
        try:
            removed = accounts_store.forget_session_owner(session_id)
            categories["ownership"] = {"status": PURGED if removed else ABSENT, "errors": []}
        except Exception as exc:  # noqa: BLE001
            categories["ownership"] = {"status": FAILED, "errors": [str(exc)]}
            logger.exception("Failed to forget owner of %s: %s", session_id, exc)
    ok = all(info["status"] in _CLEAN_STATUSES for info in categories.values())
    return {"ok": ok, "session_id": session_id, "categories": categories}


def purge_account(account_id: str) -> Dict[str, Any]:
    """Erase everything this platform holds for ``account_id``.

    Order: vector collections, then on-disk session trees (state snapshot,
    uploaded files, extracted text, deploy artefacts, container rules), then
    Google Drive tokens and file metadata, then the workbench workspace, then
    the in-memory session cache, and only then the accounts database.

    The accounts row is removed even when a content category failed. The
    alternative — refusing to erase the identity because a file handle was
    locked — leaves the user's email, password hash and billing identifiers in
    place indefinitely. What is *not* hidden is the consequence: the failing
    categories are named, ``ok`` is False, and ``residual_session_ids`` carries
    the ids an operator needs to finish the job by hand, since the ownership
    index that would have found them again is gone.

    Returns a report; never raises for a partial purge.
    """
    session_ids = accounts_store.sessions_for_owner(account_id)
    safe_ids, unsafe_ids = _partition_session_ids(session_ids)
    roots = _storage_roots()
    categories: Dict[str, Any] = {}

    # 1. Vectors first — see module docstring (resurrection path).
    #    Chroma names are sanitised by ``collection_name``, so every id is safe
    #    to pass here; only filesystem paths need the partition.
    categories["vector_index"] = _purge_vector_index(session_ids)

    # 2. Session trees: state.json, files/, deploy/, container/.
    session_paths = [root / "sessions" / sid for root in roots for sid in safe_ids]
    categories["session_storage"] = _purge_trees(session_paths, roots)

    # 3. Google Drive OAuth tokens, bindings, jobs and file metadata.
    drive_paths = [root / "google_drive" / sid for root in roots for sid in safe_ids]
    categories["google_drive"] = _purge_trees(drive_paths, roots)

    # 4. Workbench sandbox workspaces.
    workbench_paths = [
        root / "workbench" / "sessions" / sid for root in roots for sid in safe_ids
    ]
    categories["workbench"] = _purge_trees(workbench_paths, roots)

    # 5. In-process cache — otherwise a purged session is still served.
    categories["session_cache"] = _evict_in_memory(session_ids)

    # 5b. Ids that cannot safely become a path were not touched on disk. Say so
    #     rather than letting the skip pass as a clean purge.
    if unsafe_ids:
        categories["unsafe_session_ids"] = {
            "status": FAILED,
            "errors": [
                f"{sid!r}: not a single path segment; on-disk content not purged"
                for sid in unsafe_ids
            ],
        }

    # 6. Accounts database: account row plus api_keys, login_tokens,
    #    session_owners and usage_counters, in one transaction.
    try:
        account_deleted = accounts_store.delete_account(account_id)
        categories["accounts_database"] = {
            "status": PURGED if account_deleted else ABSENT,
            "errors": [],
        }
    except Exception as exc:  # noqa: BLE001
        account_deleted = False
        categories["accounts_database"] = {"status": FAILED, "errors": [str(exc)]}
        logger.exception("Failed to delete account %s: %s", account_id, exc)

    purged = [name for name, info in categories.items() if info["status"] in _CLEAN_STATUSES]
    not_purged = [
        {"category": name, "status": info["status"], "errors": info.get("errors", [])}
        for name, info in categories.items()
        if info["status"] not in _CLEAN_STATUSES
    ]
    ok = not not_purged and account_deleted

    return {
        "account_id": account_id,
        "ok": ok,
        "account_deleted": account_deleted,
        "session_ids": session_ids,
        "categories": categories,
        "purged": purged,
        "not_purged": not_purged,
        "residual_session_ids": session_ids if not_purged else [],
        "not_reachable_by_design": _structural_residue(),
    }


def export_account(account_id: str) -> Dict[str, Any] | None:
    """Everything the platform holds about an account, as one JSON document.

    Secret material is excluded by construction: the underlying store builds
    the payload from an explicit field whitelist, so password hashes, token
    hashes and raw tokens are never candidates for inclusion.
    """
    payload = accounts_store.export_account(account_id)
    if payload is None:
        return None
    payload["export_notes"] = {
        "excluded_secrets": (
            "Password hashes, API key hashes and login/verification/reset token "
            "hashes are deliberately omitted. They are credential material, not "
            "personal data, and exporting them would create a new attack surface."
        ),
        "session_content": (
            "The session list is by id. Session content — chat history, "
            "uploaded documents and extracted text — is retrieved per session "
            "through the sessions API."
        ),
        "external_processors": (
            "Stripe holds the billing records behind the identifiers above."
        ),
    }
    return payload


def run_retention_pass() -> Dict[str, Any]:
    """Delete credential material whose lifetime has elapsed.

    Callable from ops, a one-shot job or the admin endpoint. Deliberately no
    scheduler and no background thread: an in-process timer on a multi-instance
    deployment gives you N concurrent passes and no way to observe or stop
    them.
    """
    result = accounts_store.purge_expired_tokens()
    return {
        "ok": True,
        **result,
        "scope": (
            "Expired login tokens and expired single-use verification/reset "
            "token hashes. Billing state (trial_ends_at, subscription_status) "
            "is not retention state and is left untouched."
        ),
    }
