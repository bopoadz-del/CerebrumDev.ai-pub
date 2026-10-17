"""Stripe billing hardening: key fallback, proration, tax opt-in, dedupe."""

from __future__ import annotations

import pytest

from app.core import accounts_store, stripe_billing


@pytest.fixture()
def _storage(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    monkeypatch.delenv("STRIPE_AUTOMATIC_TAX", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_probe")
    # The store caches one engine per database URL; clear it so the tmp
    # ACCOUNTS_DB_PATH is what every assertion below hits.
    import app.core.accounts_store as store

    store._ENGINES.clear()
    return tmp_path


def _account(email="billing@example.invalid"):
    return accounts_store.create_account(email, "probe-password-123")


def test_secret_key_falls_back_to_stripe_api_key(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_from_render")
    assert stripe_billing.secret_key() == "sk_test_from_render"

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_canonical")
    assert stripe_billing.secret_key() == "sk_test_canonical"


def test_publishable_key_in_the_secret_slot_is_refused(monkeypatch):
    """The mistake this guard exists for: pk_... pasted into the secret env."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "pk_test_public_key")
    with pytest.raises(stripe_billing.StripeKeyError) as exc:
        stripe_billing.assert_secret_key_shape()
    assert stripe_billing.PUBLISHABLE_AS_SECRET in str(exc.value)


def test_checkout_prorates_and_tax_is_opt_in(monkeypatch, _storage):
    captured = {}

    def fake_create(**params):
        captured.update(params)
        return type("S", (), {"url": "https://checkout.stripe.test"})()  # noqa: F821

    monkeypatch.setattr(stripe_billing.stripe.checkout.Session, "create", fake_create)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_AUTOMATIC_TAX", "1")

    stripe_billing.create_checkout_session("acct", "a@b.c")

    assert captured["subscription_data"]["proration_behavior"] == "create_prorations"
    assert captured["automatic_tax"] == {"enabled": True}


def test_tax_stays_off_without_the_flag(monkeypatch, _storage):
    captured = {}

    def fake_create(**params):
        captured.update(params)
        return type("S", (), {"url": "https://checkout.stripe.test"})()  # noqa: F821

    monkeypatch.setattr(stripe_billing.stripe.checkout.Session, "create", fake_create)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")

    stripe_billing.create_checkout_session("acct", "a@b.c")

    assert "automatic_tax" not in captured


def test_invoice_paid_recovers_the_account(monkeypatch, _storage):
    account = _account()
    accounts_store.set_subscription(
        account["account_id"], "past_due", stripe_customer_id="cus_1"
    )
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")

    result = stripe_billing.handle_event(
        {
            "id": "evt_paid_1",
            "type": "invoice.paid",
            "data": {"object": {"customer": "cus_1"}},
        }
    )
    assert result["handled"] is True
    assert result["status"] == "active"
    fields = accounts_store.subscription_fields(account["account_id"])
    assert fields["subscription_status"] == "active"


def test_redelivered_event_is_acked_not_reapplied(monkeypatch, _storage):
    account = _account()
    applied = []
    original_set = accounts_store.set_subscription

    def spy_set(*args, **kwargs):
        applied.append(args)
        return original_set(*args, **kwargs)

    monkeypatch.setattr(accounts_store, "set_subscription", spy_set)
    event = {
        "id": "evt_checkout_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": account["account_id"],
                "customer": "cus_2",
                "subscription": "sub_2",
            }
        },
    }
    first = stripe_billing.handle_event(event)
    second = stripe_billing.handle_event(event)

    assert first["handled"] is True
    assert second == {"handled": False, "type": event["type"], "reason": "duplicate event"}
    assert len(applied) == 1
