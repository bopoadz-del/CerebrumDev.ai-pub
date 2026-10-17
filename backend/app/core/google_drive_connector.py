"""Reusable, domain-neutral Google Drive connector core.

This module provides the OAuth flow, token storage, and Drive API helpers used
by both the CerebrumDev.ai factory and every generated client platform. It is
intentionally storage-agnostic at the edges: the factory keeps records in
JSON files under ``STORAGE_PATH/google_drive``, while generated platforms may
persist the same models through SQLAlchemy.

Security rules enforced here:
* read-only Drive scope by default
* per-user/per-session tokens
* encrypted refresh tokens at rest via ``DATA_ENCRYPTION_KEY``
* no tokens in API responses (callers receive metadata only)
* OAuth state is single-use and time-bounded
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from app.core import file_crypto

logger = logging.getLogger(__name__)

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_API = "https://www.googleapis.com/drive/v3"
_DEFAULT_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


class DriveNotConnected(Exception):
    """No Google Drive connection exists for the requested scope."""


class DriveAuthError(Exception):
    """Token exchange or refresh failed."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class DriveConnection(BaseModel):
    connection_id: str
    session_id: str
    user_id: str
    email: str | None = None
    status: Literal["active", "disconnected"] = "active"
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DriveFolderBinding(BaseModel):
    binding_id: str
    connection_id: str
    session_id: str
    folder_id: str
    folder_name: str
    bound_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DriveSyncJob(BaseModel):
    job_id: str
    binding_id: str
    connection_id: str
    session_id: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    imported_count: int = 0
    failed_count: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DriveFileRecord(BaseModel):
    record_id: str
    job_id: str
    binding_id: str
    session_id: str
    drive_file_id: str
    name: str
    mime_type: str | None = None
    imported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditEvent(BaseModel):
    event_id: str
    event_type: Literal[
        "drive.connected",
        "drive.disconnected",
        "drive.folder_bound",
        "drive.sync.started",
        "drive.sync.completed",
        "drive.sync.failed",
        "drive.file.imported",
        "drive.file.deleted",
    ]
    session_id: str | None = None
    user_id: str | None = None
    connection_id: str | None = None
    binding_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Filesystem-safe identifiers
# ---------------------------------------------------------------------------


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "")).strip("_")
    if not cleaned:
        raise DriveAuthError("A non-empty identifier is required for storage.")
    return cleaned


# ---------------------------------------------------------------------------
# OAuth configuration helpers
# ---------------------------------------------------------------------------


def configured() -> bool:
    """Return True when Google OAuth credentials are present."""
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/v1/sessions/drive/callback",
    )


def frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")


def build_auth_url(state: str) -> str:
    """Return the Google consent URL for the factory/generated flow."""
    if not configured():
        raise DriveAuthError("Google Drive is not configured.")
    params = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": _DEFAULT_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange / refresh (overridable seams for tests)
# ---------------------------------------------------------------------------


async def exchange_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for tokens."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise DriveAuthError(f"Code exchange failed (HTTP {resp.status_code})")
    return resp.json()


async def fetch_email(access_token: str) -> str:
    """Read the connected account's email via the Drive ``about`` endpoint."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_DRIVE_API}/about",
            params={"fields": "user"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        return ""
    return resp.json().get("user", {}).get("emailAddress", "")


async def _refresh_request(refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        google_reason = ""
        try:
            body = resp.json()
            google_reason = body.get("error_description") or body.get("error") or ""
        except Exception:
            google_reason = (resp.text or "")[:200]
        raise DriveAuthError(
            f"Token refresh failed (HTTP {resp.status_code}: {google_reason})"
            if google_reason
            else f"Token refresh failed (HTTP {resp.status_code})"
        )
    return resp.json()


async def get_access_token(
    store: "DriveConnectionStore",
    session_id: str,
    connection_id: str,
) -> str:
    """Return a valid access token, refreshing if necessary."""
    token = store.load_token(session_id, connection_id)
    if not token:
        raise DriveNotConnected("Google Drive is not connected.")
    if token.get("expiry", 0) > time.time() + 60:
        return token["access_token"]
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise DriveAuthError("No refresh token stored — reconnect Google Drive.")
    data = await _refresh_request(refresh_token)
    token["access_token"] = data["access_token"]
    token["expiry"] = time.time() + int(data.get("expires_in", 3600))
    store.save_token(session_id, connection_id, token)
    return token["access_token"]


# ---------------------------------------------------------------------------
# Drive API helpers
# ---------------------------------------------------------------------------


async def list_files(
    access_token: str,
    folder_id: str | None = None,
    q: str = "",
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """List files and folders from Drive.

    When ``folder_id`` is None, lists the user's root-level items. ``q`` is a
    Drive search query; when provided, folder_id is ignored (Drive search is
    global).
    """
    async with httpx.AsyncClient(timeout=20) as client:
        if q:
            query = f"({q}) and trashed=false"
        elif folder_id:
            query = f"'{folder_id}' in parents and trashed=false"
        else:
            query = "'root' in parents and trashed=false"

        params: dict[str, Any] = {
            "q": query,
            "pageSize": page_size,
            "orderBy": "folder,name",
            "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        out: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(
                f"{_DRIVE_API}/files",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
            out.extend(payload.get("files", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return out


# ---------------------------------------------------------------------------
# Factory file-system store
# ---------------------------------------------------------------------------


class DriveConnectionStore:
    """File-based store for Drive connections, bindings, jobs, and file records.

    Token files are written through ``file_crypto`` so they are encrypted at
    rest when ``DATA_ENCRYPTION_KEY`` is set. Metadata files are plain JSON.
    """

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(
            base_dir or os.getenv("STORAGE_PATH", "./storage")
        ).resolve() / "google_drive"

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / _safe_id(session_id)

    def _connections_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "connections"

    def _tokens_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "tokens"

    def _bindings_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "bindings"

    def _jobs_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "jobs"

    def _files_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "files"

    # Connections ----------------------------------------------------------

    def save_connection(self, connection: DriveConnection) -> None:
        path = (
            self._connections_dir(connection.session_id)
            / f"{_safe_id(connection.connection_id)}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(connection.model_dump_json(), encoding="utf-8")

    def load_connection(self, session_id: str, connection_id: str) -> DriveConnection | None:
        path = self._connections_dir(session_id) / f"{_safe_id(connection_id)}.json"
        if not path.exists():
            return None
        return DriveConnection.model_validate_json(path.read_text(encoding="utf-8"))

    def list_connections(self, session_id: str) -> list[DriveConnection]:
        directory = self._connections_dir(session_id)
        if not directory.exists():
            return []
        connections: list[DriveConnection] = []
        for path in sorted(directory.glob("*.json")):
            try:
                connections.append(
                    DriveConnection.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping corrupt connection file %s: %s", path, exc)
        return connections

    def delete_connection(self, session_id: str, connection_id: str) -> bool:
        conn_path = self._connections_dir(session_id) / f"{_safe_id(connection_id)}.json"
        token_path = self._tokens_dir(session_id) / f"{_safe_id(connection_id)}.json"
        removed = False
        for path in (conn_path, token_path):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    # Tokens ---------------------------------------------------------------

    def save_token(
        self,
        session_id: str,
        connection_id: str,
        token: dict[str, Any],
    ) -> None:
        path = self._tokens_dir(session_id) / f"{_safe_id(connection_id)}.json"
        file_crypto.write_document(str(path), json.dumps(token).encode("utf-8"))

    def load_token(self, session_id: str, connection_id: str) -> dict[str, Any] | None:
        path = self._tokens_dir(session_id) / f"{_safe_id(connection_id)}.json"
        if not path.exists():
            return None
        try:
            return json.loads(file_crypto.read_document(str(path)).decode("utf-8"))
        except (file_crypto.DecryptionError, ValueError, UnicodeDecodeError) as exc:
            logger.warning(
                "Drive token for session=%s connection=%s is unreadable (%s); clearing it.",
                session_id,
                connection_id,
                type(exc).__name__,
            )
            try:
                path.unlink()
            except OSError:
                pass
            return None

    # Bindings -------------------------------------------------------------

    def save_binding(self, binding: DriveFolderBinding) -> None:
        path = (
            self._bindings_dir(binding.session_id)
            / f"{_safe_id(binding.binding_id)}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(binding.model_dump_json(), encoding="utf-8")

    def list_bindings(self, session_id: str) -> list[DriveFolderBinding]:
        directory = self._bindings_dir(session_id)
        if not directory.exists():
            return []
        bindings: list[DriveFolderBinding] = []
        for path in sorted(directory.glob("*.json")):
            try:
                bindings.append(
                    DriveFolderBinding.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping corrupt binding file %s: %s", path, exc)
        return bindings

    def load_binding(self, session_id: str, binding_id: str) -> DriveFolderBinding | None:
        path = self._bindings_dir(session_id) / f"{_safe_id(binding_id)}.json"
        if not path.exists():
            return None
        return DriveFolderBinding.model_validate_json(path.read_text(encoding="utf-8"))

    # Jobs -----------------------------------------------------------------

    def save_job(self, job: DriveSyncJob) -> None:
        path = self._jobs_dir(job.session_id) / f"{_safe_id(job.job_id)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(job.model_dump_json(), encoding="utf-8")

    def load_job(self, session_id: str, job_id: str) -> DriveSyncJob | None:
        path = self._jobs_dir(session_id) / f"{_safe_id(job_id)}.json"
        if not path.exists():
            return None
        return DriveSyncJob.model_validate_json(path.read_text(encoding="utf-8"))

    # File records ---------------------------------------------------------

    def save_file_record(self, record: DriveFileRecord) -> None:
        path = (
            self._files_dir(record.session_id)
            / f"{_safe_id(record.record_id)}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(), encoding="utf-8")

    def list_file_records(
        self,
        session_id: str,
        job_id: str | None = None,
    ) -> list[DriveFileRecord]:
        directory = self._files_dir(session_id)
        if not directory.exists():
            return []
        records: list[DriveFileRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                record = DriveFileRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping corrupt file record %s: %s", path, exc)
                continue
            if job_id is None or record.job_id == job_id:
                records.append(record)
        return records

    # Audit events ---------------------------------------------------------

    def append_audit_event(self, event: AuditEvent) -> None:
        directory = self._session_dir(event.session_id or "global") / "audit"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{event.event_id}.json"
        path.write_text(event.model_dump_json(), encoding="utf-8")
