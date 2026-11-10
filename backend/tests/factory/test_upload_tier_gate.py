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


@pytest.mark.parametrize("status", ["active", "trialing"])
def test_entitled_principal_uploads_and_indexes(gate_client, status):
    """An entitled user (active sub, or live trial) uploads: /upload/allowed
    returns real caps and the POST proceeds (background index scheduled). Two
    inputs (two entitled statuses)."""
    client, create_session, started = gate_client
    body = _register(client)  # starts trialing with a live trial
    headers = _auth(body)

    if status == "active":
        from app.core import accounts_store

        assert accounts_store.set_subscription(body["account_id"], "active")

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


def _principal(kind, account_id=None):
    from app.core.auth import Principal

    return Principal(kind=kind, account_id=account_id)


@pytest.mark.parametrize(
    "status,ends,expected",
    [
        ("active", None, True),
        ("trialing", "2999-01-01T00:00:00+00:00", True),
        ("trialing", "2000-01-01T00:00:00+00:00", False),
        ("none", None, False),
    ],
)
def test_is_principal_entitled_derives_from_plan(monkeypatch, status, ends, expected):
    """The upload gate reads the principal's plan status; admin/dev pass, and a
    user passes only when active or inside a live trial. Two inputs."""
    from app.core import accounts_store, billing

    monkeypatch.setattr(
        accounts_store,
        "subscription_fields",
        lambda account_id: {"subscription_status": status, "trial_ends_at": ends},
    )
    assert billing.is_principal_entitled(_principal("user", "acct-1")) is expected
    assert billing.is_principal_entitled(_principal("admin")) is True
    assert billing.is_principal_entitled(_principal("dev")) is True


@pytest.mark.parametrize(
    "status,ends,raises",
    [
        ("active", None, False),
        ("trialing", "2999-01-01T00:00:00+00:00", False),
        ("trialing", "2000-01-01T00:00:00+00:00", True),
        ("none", None, True),
    ],
)
def test_assert_upload_entitled_refuses_non_entitled(monkeypatch, status, ends, raises):
    """The POST gate refuses a non-entitled user with FeatureGateDenied carrying
    document_upload, and returns the principal for an entitled user. Two inputs."""
    from app.core import accounts_store, billing

    monkeypatch.setattr(
        accounts_store,
        "subscription_fields",
        lambda account_id: {"subscription_status": status, "trial_ends_at": ends},
    )
    principal = _principal("user", "acct-1")
    if raises:
        with pytest.raises(billing.FeatureGateDenied) as exc:
            billing.assert_upload_entitled(principal)
        assert exc.value.feature == "document_upload"
    else:
        assert billing.assert_upload_entitled(principal) is principal


def test_assert_upload_entitled_admin_and_dev_pass(monkeypatch):
    from app.core import accounts_store, billing

    monkeypatch.setattr(accounts_store, "subscription_fields", lambda account_id: None)
    assert billing.assert_upload_entitled(_principal("admin")) is not None
    assert billing.assert_upload_entitled(_principal("dev")) is not None
