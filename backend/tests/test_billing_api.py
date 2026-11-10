"""P3 billing: trials, entitlement, enforcement."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    storage_path = str(tmp_path / "storage")
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    import app.core.session_persistence as session_persistence

    monkeypatch.setattr(session_persistence, "STORAGE_PATH", storage_path)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # No master key now means "refuse", not "let everyone in", so an
    # unauthenticated fixture has to ask for the dev principal.
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    for var in (
        "SMTP_HOST",
        "ACCOUNTS_REQUIRE_VERIFIED_EMAIL",
        "ACCOUNTS_DB_PATH",
        "ACCOUNTS_DATABASE_URL",
        "AUTH_RATE_LIMIT_MAX",
        "AUTH_RATE_LIMIT_WINDOW_S",
        "TRIAL_DAYS",
        "BILLING_ENFORCEMENT",
    ):
        monkeypatch.delenv(var, raising=False)

    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    from app.routers import accounts, billing, sessions

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(billing.router, prefix="/v1/billing")
    app.include_router(sessions.router, prefix="/v1/sessions")
    return TestClient(app)


def _register(client, email="dev@example.com", password="pilot-pass-123"):
    res = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def _auth(body):
    return {"Authorization": f"Bearer {body['login_token']}"}


def test_register_starts_free_trial(client):
    body = _register(client)
    snap = body["billing"]
    assert snap["subscription_status"] == "trialing"
    assert snap["trial_ends_at"]
    assert snap["trial_days_left"] == 3  # default TRIAL_DAYS
    assert snap["entitled"] is True
    assert snap["enforcement"] is False

    res = client.get("/v1/billing/status", headers=_auth(body))
    assert res.status_code == 200
    assert res.json()["billing"]["subscription_status"] == "trialing"
    assert res.json()["billing"]["entitled"] is True


def test_trial_days_env_respected(client, monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "7")
    body = _register(client)
    assert body["billing"]["trial_days_left"] == 7


def test_billing_status_requires_credential(client):
    assert client.get("/v1/billing/status").status_code == 403


def test_expired_trial_allowed_while_enforcement_off(client, monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "-1")  # trial already over at registration
    body = _register(client)
    assert body["billing"]["entitled"] is False
    assert body["billing"]["trial_days_left"] == 0
    # Enforcement off by default → nothing breaks for existing deployments.
    res = client.post("/v1/sessions/", headers=_auth(body))
    assert res.status_code == 200, res.text


def test_enforcement_blocks_expired_trial(client, monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "-1")
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    body = _register(client)

    res = client.post("/v1/sessions/", headers=_auth(body))
    assert res.status_code == 402
    assert res.json()["detail"] == "trial_expired"

    snap = client.get("/v1/billing/status", headers=_auth(body)).json()["billing"]
    assert snap["entitled"] is False
    assert snap["trial_days_left"] == 0
    assert snap["enforcement"] is True


def test_active_subscription_restores_access(client, monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "-1")
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    body = _register(client)

    # This is the seam the Stripe webhook uses (checkout slice lands next).
    from app.core import accounts_store

    assert accounts_store.set_subscription(body["account_id"], "active", "cus_test_123")

    res = client.post("/v1/sessions/", headers=_auth(body))
    assert res.status_code == 200, res.text
    snap = client.get("/v1/billing/status", headers=_auth(body)).json()["billing"]
    assert snap["subscription_status"] == "active"
    assert snap["entitled"] is True


def test_canceled_subscription_blocked(client, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    body = _register(client)  # live trial → passes first
    assert client.post("/v1/sessions/", headers=_auth(body)).status_code == 200

    from app.core import accounts_store

    accounts_store.set_subscription(body["account_id"], "canceled")
    res = client.post("/v1/sessions/", headers=_auth(body))
    assert res.status_code == 402


def test_admin_and_dev_bypass_enforcement(client, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    # Dev principal (no credentials, no master key) — local dev unaffected.
    res = client.post("/v1/sessions/")
    assert res.status_code == 200
    # Admin master key.
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.post("/v1/sessions/", headers={"Authorization": "Bearer master-secret"})
    assert res.status_code == 200


def test_legacy_account_without_billing_fields_not_entitled(client, monkeypatch):
    """Accounts predating P3 (NULL billing columns → status 'none') are not
    silently entitled — ops grants them explicitly via set_subscription."""
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    body = _register(client)

    from app.core import accounts_store

    # Simulate a pre-P3 row by clearing the billing fields.
    engine = accounts_store._engine()
    with engine.begin() as conn:
        conn.execute(
            accounts_store._t_accounts.update()
            .where(accounts_store._t_accounts.c.id == body["account_id"])
            .values(subscription_status=None, trial_ends_at=None)
        )

    snap = client.get("/v1/billing/status", headers=_auth(body)).json()["billing"]
    assert snap["subscription_status"] == "none"
    assert snap["entitled"] is False
    res = client.post("/v1/sessions/", headers=_auth(body))
    assert res.status_code == 402
