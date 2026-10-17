"""Tests for the CerebrumDev.ai factory Google Drive integration.

These tests prove:
* OAuth connect/callback creates a connection and encrypts the token.
* Drive status is scoped to a session (wrong session -> 404).
* Disconnect removes the token.
* File listing, folder binding, and sync are reachable and isolated.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core import google_drive_connector as connector
from app.core.google_drive_connector import DriveConnectionStore
from app.routers import factory_drive


@pytest.fixture
def encryption_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def drive_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DriveConnectionStore:
    """Replace the module-global store with an isolated tmp store."""
    # Ensure env is configured for Drive routes.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = DriveConnectionStore(base_dir=tmp_path)
    factory_drive._store = store
    factory_drive._pending_states.clear()
    return store


def _create_session(client: TestClient) -> str:
    resp = client.post("/v1/sessions/")
    assert resp.status_code == 200
    return resp.json()["session_id"]


def test_drive_connect_requires_configuration(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    session_id = _create_session(client)
    resp = client.get(f"/v1/sessions/{session_id}/drive/connect")
    assert resp.status_code == 503


def test_drive_connect_returns_auth_url(client: TestClient, drive_store: DriveConnectionStore):
    session_id = _create_session(client)
    resp = client.get(f"/v1/sessions/{session_id}/drive/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert "auth_url" in data
    assert "state=" in data["auth_url"]


def test_drive_callback_creates_connection(
    client: TestClient,
    drive_store: DriveConnectionStore,
):
    session_id = _create_session(client)
    connect_resp = client.get(f"/v1/sessions/{session_id}/drive/connect")
    auth_url = connect_resp.json()["auth_url"]
    state = _extract_state(auth_url)

    async def fake_exchange(_code: str) -> dict:
        return {
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "expires_in": 3600,
        }

    async def fake_email(_token: str) -> str:
        return "factory-user@example.com"

    with patch.object(factory_drive, "exchange_code", fake_exchange), patch.object(
        factory_drive, "fetch_email", fake_email
    ):
        resp = client.get(
            "/v1/sessions/drive/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "drive=connected" in resp.headers.get("location", "")

    # Status shows the connection but does not leak tokens.
    status_resp = client.get(f"/v1/sessions/{session_id}/drive/status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["connected"] is True
    assert status["connections"][0]["email"] == "factory-user@example.com"
    assert "access_token" not in str(status)
    assert "refresh_token" not in str(status)

    # Token file is encrypted when DATA_ENCRYPTION_KEY is set.
    token_files = list((drive_store.base_dir / session_id / "tokens").rglob("*.json"))
    assert len(token_files) == 1
    raw = token_files[0].read_bytes()
    assert connector.file_crypto.looks_encrypted(raw)


def test_drive_status_404_for_wrong_session(client: TestClient, drive_store: DriveConnectionStore):
    bad_session = f"sess_{uuid.uuid4().hex[:16]}"
    resp = client.get(f"/v1/sessions/{bad_session}/drive/status")
    assert resp.status_code == 404


def test_drive_disconnect_clears_token(
    client: TestClient,
    drive_store: DriveConnectionStore,
):
    session_id = _create_session(client)
    connect_resp = client.get(f"/v1/sessions/{session_id}/drive/connect")
    state = _extract_state(connect_resp.json()["auth_url"])

    async def fake_exchange(_code: str) -> dict:
        return {"access_token": "a", "refresh_token": "b", "expires_in": 3600}

    async def fake_email(_t: str) -> str:
        return "a@b.com"

    with patch.object(factory_drive, "exchange_code", fake_exchange), patch.object(
        factory_drive, "fetch_email", fake_email
    ):
        client.get(
            "/v1/sessions/drive/callback",
            params={"code": "x", "state": state},
            follow_redirects=False,
        )

    assert client.get(f"/v1/sessions/{session_id}/drive/status").json()["connected"] is True
    resp = client.post(f"/v1/sessions/{session_id}/drive/disconnect")
    assert resp.status_code == 200
    assert client.get(f"/v1/sessions/{session_id}/drive/status").json()["connected"] is False


def test_drive_files_requires_connection(client: TestClient, drive_store: DriveConnectionStore):
    session_id = _create_session(client)
    resp = client.get(f"/v1/sessions/{session_id}/drive/files")
    assert resp.status_code == 409


def test_drive_files_listing_is_session_scoped(
    client: TestClient,
    drive_store: DriveConnectionStore,
):
    session_id = _create_session(client)
    _connect_session(client, session_id, "lister@example.com")

    async def fake_list(*_args, **_kwargs) -> list[dict]:
        return [{"id": "file-1", "name": "recall.pdf", "mimeType": "application/pdf"}]

    with patch.object(factory_drive, "list_files", fake_list):
        resp = client.get(f"/v1/sessions/{session_id}/drive/files")

    assert resp.status_code == 200
    assert resp.json()["files"][0]["name"] == "recall.pdf"


def test_drive_folder_binding_and_sync(
    client: TestClient,
    drive_store: DriveConnectionStore,
):
    session_id = _create_session(client)
    _connect_session(client, session_id, "syncer@example.com")

    bind_resp = client.post(
        f"/v1/sessions/{session_id}/drive/bind-folder",
        json={"folder_id": "folder-123", "folder_name": "Safety Reports"},
    )
    assert bind_resp.status_code == 200
    binding_id = bind_resp.json()["binding_id"]

    bindings_resp = client.get(f"/v1/sessions/{session_id}/drive/bindings")
    assert bindings_resp.status_code == 200
    assert len(bindings_resp.json()["bindings"]) == 1

    async def fake_list(*_args, **_kwargs) -> list[dict]:
        return [
            {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf"},
            {"id": "f2", "name": "subfolder", "mimeType": "application/vnd.google-apps.folder"},
        ]

    with patch.object(factory_drive, "list_files", fake_list):
        sync_resp = client.post(f"/v1/sessions/{session_id}/drive/sync/{binding_id}")
    assert sync_resp.status_code == 202
    job_id = sync_resp.json()["job_id"]

    status_resp = client.get(f"/v1/sessions/{session_id}/drive/sync/{job_id}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["imported_count"] == 1


def test_cross_session_drive_isolation(
    client: TestClient,
    drive_store: DriveConnectionStore,
):
    session_a = _create_session(client)
    session_b = _create_session(client)
    _connect_session(client, session_a, "a@example.com")

    # Session B sees no connection.
    assert client.get(f"/v1/sessions/{session_b}/drive/status").json()["connected"] is False
    # Session B cannot list files using session A's connection.
    assert client.get(f"/v1/sessions/{session_b}/drive/files").status_code == 409


def _extract_state(auth_url: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(auth_url).query)["state"][0]


def _connect_session(client: TestClient, session_id: str, email: str) -> str:
    connect_resp = client.get(f"/v1/sessions/{session_id}/drive/connect")
    state = _extract_state(connect_resp.json()["auth_url"])


    async def fake_exchange(_code: str) -> dict:
        return {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600}

    async def fake_email(_token: str) -> str:
        return email

    with patch.object(factory_drive, "exchange_code", fake_exchange), patch.object(
        factory_drive, "fetch_email", fake_email
    ):
        client.get(
            "/v1/sessions/drive/callback",
            params={"code": "x", "state": state},
            follow_redirects=False,
        )

    connections = factory_drive._store.list_connections(session_id)
    assert connections
    return connections[0].connection_id
