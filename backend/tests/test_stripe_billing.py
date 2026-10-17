"""P3 billing slice B: Stripe checkout, portal, webhook handling.

All Stripe SDK calls are monkeypatched — no network, no real keys.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import stripe
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
        "STRIPE_SECRET_KEY",
        "STRIPE_PRICE_ID",
        "STRIPE_WEBHOOK_SECRET",
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


@pytest.fixture()
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")


def _register(client, email="dev@example.com", password="pilot-pass-123"):
    res = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def _auth(body):
    return {"Authorization": f"Bearer {body['login_token']}"}


def _billing(client, body):
    res = client.get("/v1/billing/status", headers=_auth(body))
    assert res.status_code == 200, res.text
    return res.json()["billing"]


# --- checkout -----------------------------------------------------------------


def test_checkout_unconfigured_returns_503(client):
    body = _register(client)
    res = client.post("/v1/billing/checkout", headers=_auth(body))
    assert res.status_code == 503
    assert res.json()["detail"] == "stripe_not_configured"
    assert _billing(client, body)["checkout_available"] is False


def test_checkout_returns_hosted_url(client, stripe_env, monkeypatch):
    body = _register(client)
    captured = {}

    def fake_create(**params):
        captured.update(params)
        return SimpleNamespace(url="https://checkout.stripe.test/session_1")

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))

    res = client.post("/v1/billing/checkout", headers=_auth(body))
    assert res.status_code == 200, res.text
    assert res.json()["checkout_url"] == "https://checkout.stripe.test/session_1"
    # New customer: identified by email + our account reference.
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_fake", "quantity": 1}]
    assert captured["client_reference_id"] == body["account_id"]
    assert captured["customer_email"] == "dev@example.com"
    assert "customer" not in captured
    assert _billing(client, body)["checkout_available"] is True


def test_checkout_existing_customer_reuses_stripe_customer(client, stripe_env, monkeypatch):
    body = _register(client)
    from app.core import accounts_store

    accounts_store.set_subscription(body["account_id"], "active", "cus_existing")

    captured = {}
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        staticmethod(
            lambda **params: captured.update(params)
            or SimpleNamespace(url="https://checkout.stripe.test/session_2")
        ),
    )
    res = client.post("/v1/billing/checkout", headers=_auth(body))
    assert res.status_code == 200
    assert captured["customer"] == "cus_existing"
    assert "customer_email" not in captured


def test_checkout_requires_credential(client, stripe_env):
    assert client.post("/v1/billing/checkout").status_code == 403


# --- portal -------------------------------------------------------------------


def test_portal_requires_existing_customer(client, stripe_env):
    body = _register(client)
    res = client.post("/v1/billing/portal", headers=_auth(body))
    assert res.status_code == 409
    assert res.json()["detail"] == "no_billing_customer"


def test_portal_returns_url(client, stripe_env, monkeypatch):
    body = _register(client)
    from app.core import accounts_store

    accounts_store.set_subscription(body["account_id"], "active", "cus_portal")
    monkeypatch.setattr(
        stripe.billing_portal.Session,
        "create",
        staticmethod(
            lambda **params: SimpleNamespace(url="https://billing.stripe.test/portal_1")
        ),
    )
    res = client.post("/v1/billing/portal", headers=_auth(body))
    assert res.status_code == 200
    assert res.json()["portal_url"] == "https://billing.stripe.test/portal_1"


# --- webhook ------------------------------------------------------------------


def _patch_event(monkeypatch, event):
    monkeypatch.setattr(
        stripe.Webhook, "construct_event", staticmethod(lambda payload, sig, secret: event)
    )


def test_webhook_unconfigured_returns_503(client):
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert res.status_code == 503


def test_webhook_rejects_bad_signature(client, stripe_env, monkeypatch):
    def raise_sig_error(payload, sig, secret):
        raise stripe.SignatureVerificationError("bad", sig)

    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(raise_sig_error))
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "forged"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "invalid_signature"


def test_webhook_checkout_completed_activates(client, stripe_env, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    monkeypatch.setenv("TRIAL_DAYS", "-1")  # trial already over → blocked
    body = _register(client)
    assert client.post("/v1/sessions/", headers=_auth(body)).status_code == 402

    _patch_event(
        monkeypatch,
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": body["account_id"],
                    "customer": "cus_web_1",
                    "subscription": "sub_web_1",
                    "metadata": {},
                }
            },
        },
    )
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert res.status_code == 200
    assert res.json()["handled"] is True

    snap = _billing(client, body)
    assert snap["subscription_status"] == "active"
    assert snap["entitled"] is True
    assert client.post("/v1/sessions/", headers=_auth(body)).status_code == 200


def test_webhook_subscription_deleted_cancels(client, stripe_env, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    body = _register(client)
    from app.core import accounts_store

    accounts_store.set_subscription(
        body["account_id"], "active", "cus_web_2", "sub_web_2"
    )
    assert client.post("/v1/sessions/", headers=_auth(body)).status_code == 200

    _patch_event(
        monkeypatch,
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_web_2", "id": "sub_web_2"}},
        },
    )
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert res.status_code == 200
    assert _billing(client, body)["subscription_status"] == "canceled"
    assert client.post("/v1/sessions/", headers=_auth(body)).status_code == 402


def test_webhook_payment_failed_marks_past_due(client, stripe_env, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")
    body = _register(client)
    from app.core import accounts_store

    accounts_store.set_subscription(body["account_id"], "active", "cus_web_3")

    _patch_event(
        monkeypatch,
        {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_web_3"}},
        },
    )
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert res.status_code == 200
    snap = _billing(client, body)
    assert snap["subscription_status"] == "past_due"
    assert snap["entitled"] is False  # past_due does not grant access


def test_webhook_unknown_event_acked(client, stripe_env, monkeypatch):
    _patch_event(monkeypatch, {"type": "ping.unrelated", "data": {"object": {}}})
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert res.status_code == 200
    assert res.json()["handled"] is False


def test_webhook_checkout_without_account_reference_acked(client, stripe_env, monkeypatch):
    """A checkout event we can't attribute must not error (Stripe would retry
    forever) — it is acked as unhandled."""
    _patch_event(
        monkeypatch,
        {
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_ghost", "metadata": {}}},
        },
    )
    res = client.post(
        "/v1/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "sig"},
    )
    assert res.status_code == 200
    assert res.json()["handled"] is False
