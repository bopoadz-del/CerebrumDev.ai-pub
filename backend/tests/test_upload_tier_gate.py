"""Upload tier gate: document upload + index is paid-tier only (Advisor 08:05Z/08:27Z)."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def gate_client(monkeypatch, tmp_path):
    """Full app + a session, with the background index stubbed, so the tier gate
    is exercised end-to-end (dependency -> exception -> handler)."""
    storage = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    for var in (
        "SMTP_HOST",
        "ACCOUNTS_REQUIRE_VERIFIED_EMAIL",
        "ACCOUNTS_DB_PATH",
        "ACCOUNTS_DATABASE_URL",
        "TRIAL_DAYS",
        "BILLING_ENFORCEMENT",
    ):
        monkeypatch.delenv(var, raising=False)

    from app.core.session_store import create_session
    from app.routers import upload as upload_router

    monkeypatch.setattr(upload_router, "STORAGE_PATH", str(storage))
    monkeypatch.setattr(
        upload_router, "process_upload", lambda sid, paths, original_names=None: (sid, paths)
    )
    started: list = []
    monkeypatch.setattr(upload_router, "start_task", lambda job_id, coro: started.append(coro))

    from app.main import app as real_app

    return TestClient(real_app), create_session, started


def _register(client):
    res = client.post(
        "/v1/auth/register",
        json={"email": f"dev-{uuid.uuid4().hex[:8]}@example.com", "password": "pilot-pass-123"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _auth(body):
    return {"Authorization": f"Bearer {body['login_token']}"}


@pytest.mark.parametrize("status", ["none", "canceled"])
def test_non_entitled_principal_is_refused_402_and_gets_inert_picker(gate_client, status):
    """Free/canceled account: GET /upload/allowed reports entitled=false with empty
    caps; POST /upload is refused 402 {detail, feature}; no index is scheduled."""
    client, create_session, started = gate_client
    body = _register(client)
    headers = _auth(body)

    from app.core import accounts_store

    assert accounts_store.set_subscription(body["account_id"], status)

    sid = f"sess_{uuid.uuid4().hex[:10]}"
    create_session(sid, body["account_id"])

    allowed = client.get(f"/v1/sessions/{sid}/upload/allowed", headers=headers)
    assert allowed.status_code == 200
    assert allowed.json()["entitled"] is False
    assert allowed.json()["extensions"] == []
    assert allowed.json()["max_files"] == 0

    resp = client.post(
        f"/v1/sessions/{sid}/upload",
        headers=headers,
        files=[("files", ("a.pdf", io.BytesIO(b"x"), "application/pdf"))],
    )
    assert resp.status_code == 402, resp.text
    assert resp.json() == {"detail": "upgrade_required", "feature": "document_upload"}
    assert started == []


def test_entitled_principal_uploads_and_indexes(gate_client):
    """A live-trial user is entitled: /upload/allowed returns real caps and the
    POST proceeds (background index scheduled)."""
    client, create_session, started = gate_client
    body = _register(client)  # trialing with a live trial
    headers = _auth(body)

    sid = f"sess_{uuid.uuid4().hex[:10]}"
    create_session(sid, body["account_id"])

    allowed = client.get(f"/v1/sessions/{sid}/upload/allowed", headers=headers).json()
    assert allowed["entitled"] is True
    assert allowed["extensions"]

    resp = client.post(
        f"/v1/sessions/{sid}/upload",
        headers=headers,
        files=[("files", ("a.pdf", io.BytesIO(b"x"), "application/pdf"))],
    )
    assert resp.status_code == 200, resp.text
    assert started
