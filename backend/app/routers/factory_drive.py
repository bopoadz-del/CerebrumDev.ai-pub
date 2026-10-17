"""CerebrumDev.ai factory Google Drive integration.

All routes are scoped to a factory session. The OAuth callback is exposed at
``/v1/sessions/drive/callback`` without API-key auth because Google cannot send
our Bearer header; it is protected by the single-use OAuth ``state`` value.

Tokens are encrypted at rest via ``DATA_ENCRYPTION_KEY`` and never returned in
API responses.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.auth import Principal, require_api_key
from app.core.session_guard import owned_session_or_404
from app.core.google_drive_connector import (
    AuditEvent,
    DriveConnection,
    DriveConnectionStore,
    DriveFileRecord,
    DriveFolderBinding,
    DriveSyncJob,
    build_auth_url,
    configured,
    exchange_code,
    fetch_email,
    frontend_url,
    get_access_token,
    list_files,
)

logger = logging.getLogger(__name__)

router = APIRouter()
callback_router = APIRouter()

_store = DriveConnectionStore()
_STATE_TTL = 600
_pending_states: dict[str, tuple[str, str, float]] = {}  # state -> (session_id, user_id, issued_at)

# Pending OAuth states live in Redis (REDIS_URL) when available so they survive
# restarts and work across instances; the in-memory dict is the fallback.
_REDIS_UNSET = object()
_redis_client: object = _REDIS_UNSET


def _redis():
    """Lazy Redis client; None when unconfigured or unreachable."""
    global _redis_client
    if _redis_client is _REDIS_UNSET:
        url = os.getenv("REDIS_URL", "").strip()
        client = None
        if url:
            try:
                import redis  # type: ignore

                client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
                client.ping()
            except Exception:
                client = None
        _redis_client = client
    return _redis_client


def _redis_required() -> bool:
    """When REDIS_URL is set, pending OAuth state must live there — no memory."""
    return bool(os.getenv("REDIS_URL", "").strip())


class OAuthStateUnavailable(RuntimeError):
    """Redis is configured but cannot store/load the OAuth pending state."""


def _state_save(state: str, session_id: str, user_id: str) -> None:
    # Multi-instance: pending state must be on Redis so the callback worker
    # that Google hits is the one that can see it. In-memory fallback is only
    # for single-process tests / local boots with REDIS_URL unset.
    if _redis_required():
        client = _redis()
        if client is None:
            raise OAuthStateUnavailable("REDIS_URL is set but Redis is unreachable")
        try:
            client.setex(
                f"drive_oauth_state:{state}",
                _STATE_TTL,
                json.dumps({"session_id": session_id, "user_id": user_id}),
            )
        except Exception as exc:  # noqa: BLE001
            raise OAuthStateUnavailable(
                "REDIS_URL is set but Redis is unreachable"
            ) from exc
        return
    _pending_states[state] = (session_id, user_id, time.time())


def _state_pop(state: str) -> tuple[str, str, float] | None:
    if _redis_required():
        client = _redis()
        if client is None:
            raise OAuthStateUnavailable("REDIS_URL is set but Redis is unreachable")
        try:
            raw = client.getdel(f"drive_oauth_state:{state}")
        except Exception as exc:  # noqa: BLE001
            raise OAuthStateUnavailable(
                "REDIS_URL is set but Redis is unreachable"
            ) from exc
        if raw is None:
            return None
        data = json.loads(raw)
        return data["session_id"], data["user_id"], 0.0
    return _pending_states.pop(state, None)


class BindFolderRequest(BaseModel):
    folder_id: str
    folder_name: str = ""


def _prune_states() -> None:
    cutoff = time.time() - _STATE_TTL
    expired = [s for s, (_, _, issued) in _pending_states.items() if issued < cutoff]
    for state in expired:
        del _pending_states[state]


def _require_session(
    session_id: str,
    principal: Principal = Depends(require_api_key),
) -> dict[str, Any]:
    """Dependency that loads the session or raises a non-leaking 404."""
    state = owned_session_or_404(session_id, principal)
    return {
        "session_id": state.session_id,
        "user_id": state.user_id,
    }


def _active_connection(session_id: str) -> DriveConnection:
    """Return the first active connection for a session, or 409."""
    connections = [c for c in _store.list_connections(session_id) if c.status == "active"]
    if not connections:
        raise HTTPException(status_code=409, detail="Google Drive is not connected.")
    return connections[0]


@router.get("/{session_id}/drive/connect")
async def drive_connect(session: dict = Depends(_require_session)):
    if not configured():
        raise HTTPException(
            status_code=503,
            detail="Google Drive not configured — set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.",
        )
    _prune_states()
    state = secrets.token_urlsafe(24)  # type: ignore[name-defined]
    try:
        _state_save(state, session["session_id"], session["user_id"])
    except OAuthStateUnavailable as exc:
        raise HTTPException(status_code=503, detail="oauth_state_unavailable") from exc
    return {"auth_url": build_auth_url(state)}


@callback_router.get("/v1/sessions/drive/callback")
async def drive_callback(
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
):
    _prune_states()
    try:
        pending = _state_pop(state)
    except OAuthStateUnavailable as exc:
        raise HTTPException(status_code=503, detail="oauth_state_unavailable") from exc
    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    session_id, user_id, _ = pending

    if error:
        return RedirectResponse(f"{frontend_url()}/?drive=error", status_code=302)
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    data = await exchange_code(code)
    access_token = data["access_token"]
    email = await fetch_email(access_token)
    connection_id = uuid.uuid4().hex[:16]

    _store.save_token(
        session_id,
        connection_id,
        {
            "access_token": access_token,
            "refresh_token": data.get("refresh_token", ""),
            "expiry": time.time() + int(data.get("expires_in", 3600)),
            "email": email,
        },
    )
    connection = DriveConnection(
        connection_id=connection_id,
        session_id=session_id,
        user_id=user_id,
        email=email,
        status="active",
    )
    _store.save_connection(connection)
    _store.append_audit_event(
        AuditEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type="drive.connected",
            session_id=session_id,
            user_id=user_id,
            connection_id=connection_id,
            details={"email": email},
        )
    )
    return RedirectResponse(f"{frontend_url()}/?drive=connected", status_code=302)


@router.get("/{session_id}/drive/status")
async def drive_status(session: dict = Depends(_require_session)):
    connections = _store.list_connections(session["session_id"])
    active = [c for c in connections if c.status == "active"]
    return {
        "configured": configured(),
        "connected": bool(active),
        "connections": [
            {
                "connection_id": c.connection_id,
                "email": c.email,
                "status": c.status,
                "connected_at": c.connected_at.isoformat(),
            }
            for c in connections
        ],
    }


@router.post("/{session_id}/drive/disconnect")
async def drive_disconnect(session: dict = Depends(_require_session)):
    active = [c for c in _store.list_connections(session["session_id"]) if c.status == "active"]
    if not active:
        raise HTTPException(status_code=409, detail="Google Drive is not connected.")
    for conn in active:
        _store.delete_connection(session["session_id"], conn.connection_id)
        _store.append_audit_event(
            AuditEvent(
                event_id=uuid.uuid4().hex[:16],
                event_type="drive.disconnected",
                session_id=session["session_id"],
                user_id=session["user_id"],
                connection_id=conn.connection_id,
            )
        )
    return {"status": "disconnected"}


@router.get("/{session_id}/drive/files")
async def drive_files(
    folder_id: str = Query(""),
    q: str = Query(""),
    session: dict = Depends(_require_session),
):
    connection = _active_connection(session["session_id"])
    access_token = await get_access_token(
        _store, session["session_id"], connection.connection_id
    )
    try:
        files = await list_files(
            access_token,
            folder_id=folder_id or None,
            q=q,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Drive list failed for session %s", session["session_id"])
        raise HTTPException(status_code=502, detail=f"Drive list failed: {exc}") from exc
    return {"files": files, "folder_id": folder_id or "root"}


@router.post("/{session_id}/drive/bind-folder")
async def drive_bind_folder(
    req: BindFolderRequest,
    session: dict = Depends(_require_session),
):
    connection = _active_connection(session["session_id"])
    binding_id = uuid.uuid4().hex[:16]
    binding = DriveFolderBinding(
        binding_id=binding_id,
        connection_id=connection.connection_id,
        session_id=session["session_id"],
        folder_id=req.folder_id,
        folder_name=req.folder_name or req.folder_id,
    )
    _store.save_binding(binding)
    _store.append_audit_event(
        AuditEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type="drive.folder_bound",
            session_id=session["session_id"],
            user_id=session["user_id"],
            connection_id=connection.connection_id,
            binding_id=binding_id,
            details={"folder_id": req.folder_id},
        )
    )
    return {"binding_id": binding_id, "folder_id": req.folder_id}


@router.get("/{session_id}/drive/bindings")
async def drive_bindings(session: dict = Depends(_require_session)):
    return {"bindings": [b.model_dump() for b in _store.list_bindings(session["session_id"])]}


async def _run_sync_job(
    session_id: str,
    user_id: str,
    binding_id: str,
    job_id: str,
) -> None:
    """Background sync: list files in the bound folder and record metadata."""
    job = _store.load_job(session_id, job_id)
    if job is None:
        return
    job.status = "running"
    job.updated_at = datetime.datetime.now(datetime.timezone.utc)
    _store.save_job(job)

    try:
        connection = _store.load_connection(session_id, job.connection_id)
        if connection is None:
            raise RuntimeError("Connection not found")
        binding = _store.load_binding(session_id, binding_id)
        if binding is None:
            raise RuntimeError("Binding not found")
        access_token = await get_access_token(_store, session_id, connection.connection_id)
        files = await list_files(access_token, folder_id=binding.folder_id)
        imported = 0
        for f in files:
            # Skip folders and unsupported native Google types for the pilot.
            mime = f.get("mimeType", "")
            if mime == "application/vnd.google-apps.folder":
                continue
            record = DriveFileRecord(
                record_id=uuid.uuid4().hex[:16],
                job_id=job_id,
                binding_id=binding_id,
                session_id=session_id,
                drive_file_id=f["id"],
                name=f.get("name", "unnamed"),
                mime_type=mime,
            )
            _store.save_file_record(record)
            imported += 1
        job.status = "done"
        job.imported_count = imported
    except Exception as exc:  # noqa: BLE001
        logger.exception("Drive sync failed session=%s job=%s", session_id, job_id)
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.failed_count += 1
    job.updated_at = datetime.datetime.now(datetime.timezone.utc)
    _store.save_job(job)
    _store.append_audit_event(
        AuditEvent(
            event_id=uuid.uuid4().hex[:16],
            event_type=(
                "drive.sync.completed" if job.status == "done" else "drive.sync.failed"
            ),
            session_id=session_id,
            user_id=user_id,
            connection_id=job.connection_id,
            binding_id=binding_id,
            details={"imported_count": imported, "error": job.error},
        )
    )


@router.post("/{session_id}/drive/sync/{binding_id}", status_code=202)
async def drive_sync(
    binding_id: str,
    background_tasks: BackgroundTasks,
    session: dict = Depends(_require_session),
):
    connection = _active_connection(session["session_id"])
    binding = _store.load_binding(session["session_id"], binding_id)
    if binding is None or binding.connection_id != connection.connection_id:
        raise HTTPException(status_code=404, detail="Folder binding not found")
    job_id = uuid.uuid4().hex[:12]
    job = DriveSyncJob(
        job_id=job_id,
        binding_id=binding_id,
        connection_id=connection.connection_id,
        session_id=session["session_id"],
        status="queued",
    )
    _store.save_job(job)
    background_tasks.add_task(
        _run_sync_job,
        session["session_id"],
        session["user_id"],
        binding_id,
        job_id,
    )
    return {
        "status": "queued",
        "job_id": job_id,
        "binding_id": binding_id,
        "status_url": f"/v1/sessions/{session['session_id']}/drive/sync/{job_id}/status",
    }


@router.get("/{session_id}/drive/sync/{job_id}/status")
async def drive_sync_status(
    job_id: str,
    session: dict = Depends(_require_session),
):
    job = _store.load_job(session["session_id"], job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return job.model_dump()
