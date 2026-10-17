"""Stripe billing (P3 slice B): checkout, customer portal, webhook handling.

Env:
  STRIPE_SECRET_KEY      — API key (sk_test_... / sk_live_...)
  STRIPE_PRICE_ID        — recurring Price for the subscription (price_...)
  STRIPE_WEBHOOK_SECRET  — signing secret for /v1/billing/webhook (whsec_...)

Checkout success/cancel return to ``FRONTEND_URL/billing``.

Everything degrades honestly: with Stripe unconfigured, checkout/portal
return 503 ``stripe_not_configured`` and the webhook is inert. Account state
only ever changes through ``accounts_store.set_subscription`` — the same seam
tests and ops use, so the Stripe path adds no new mutation surface.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Final, Optional

import stripe

from . import accounts_store


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def secret_key() -> str:
    """Secret key, accepting the two names deployments actually use:
    STRIPE_SECRET_KEY (canonical) or STRIPE_API_KEY (Render env)."""
    return _env("STRIPE_SECRET_KEY") or _env("STRIPE_API_KEY")


def price_id() -> str:
    return _env("STRIPE_PRICE_ID")


def webhook_secret() -> str:
    return _env("STRIPE_WEBHOOK_SECRET")


def stripe_configured() -> bool:
    return bool(secret_key() and price_id())


#: Refused when a publishable key (pk_...) is placed in the secret slot.
#: A pk_ key can never drive server calls; failing here is fail-loud —
#: the silent version is a checkout that 401s at Stripe with no reason.
PUBLISHABLE_AS_SECRET: Final = "stripe_publishable_key_used_as_secret"


class StripeKeyError(ValueError):
    """A named refusal about Stripe credential placement."""


def assert_secret_key_shape() -> None:
    """The secret slot must hold a secret key, never a publishable one."""
    key = secret_key()
    if key.startswith("pk_"):
        raise StripeKeyError(
            f"{PUBLISHABLE_AS_SECRET}: STRIPE_SECRET_KEY holds a publishable "
            "key (pk_...) — publishable keys belong in the frontend env "
            "(STRIPE_PUBLISHABLE_KEY), never in the server secret slot"
        )


def _frontend_url() -> str:
    return _env("FRONTEND_URL") or "http://localhost:5173"


def _configure() -> None:
    assert_secret_key_shape()
    stripe.api_key = secret_key()


def _automatic_tax_enabled() -> bool:
    """Opt-in: automatic_tax only when Stripe Tax is on for the account.
    Enabling it on an account without Tax fails checkout, so the flag is
    explicit instead of assumed."""
    return _env("STRIPE_AUTOMATIC_TAX").lower() in {"1", "true", "yes", "on"}


def create_checkout_session(account_id: str, email: str) -> str:
    """Create a subscription Checkout Session; returns the hosted URL.

    Returning customers (already have a Stripe customer id) are attached to
    their existing customer so their payment method carries over.
    """
    _configure()
    fields = accounts_store.subscription_fields(account_id) or {}
    subscription_data: Dict[str, Any] = {
        "metadata": {"account_id": account_id},
        # Phase B: plan changes prorate rather than bill on the next cycle.
        "proration_behavior": "create_prorations",
    }
    params: Dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id(), "quantity": 1}],
        "client_reference_id": account_id,
        "metadata": {"account_id": account_id},
        "subscription_data": subscription_data,
        "success_url": f"{_frontend_url()}/billing?checkout=success",
        "cancel_url": f"{_frontend_url()}/billing?checkout=cancel",
    }
    if _automatic_tax_enabled():
        params["automatic_tax"] = {"enabled": True}
    if fields.get("stripe_customer_id"):
        params["customer"] = fields["stripe_customer_id"]
    else:
        params["customer_email"] = email
    session = stripe.checkout.Session.create(**params)
    return session.url


def create_portal_session(account_id: str) -> Optional[str]:
    """Customer portal URL (update card, cancel, invoices); None if the
    account has never checked out (no Stripe customer yet)."""
    _configure()
    fields = accounts_store.subscription_fields(account_id) or {}
    customer = fields.get("stripe_customer_id")
    if not customer:
        return None
    session = stripe.billing_portal.Session.create(
        customer=customer,
        return_url=f"{_frontend_url()}/billing",
    )
    return session.url


def construct_webhook_event(payload: bytes, signature: str) -> Dict[str, Any]:
    """Verify the Stripe signature and return the event (raises on failure)."""
    return stripe.Webhook.construct_event(payload, signature, webhook_secret())


# Stripe subscription statuses mapped onto our four states. Anything that
# isn't clearly good ('active'/'trialing') must not grant access.
_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
    "paused": "canceled",
}


def _account_id_for(customer: Optional[str], metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve our account from a Stripe customer id, falling back to the
    metadata we stamp on checkout/subscription."""
    if customer:
        account = accounts_store.account_for_stripe_customer(customer)
        if account is not None:
            return account["account_id"]
    if metadata:
        account_id = (metadata.get("account_id") or "").strip()
        if account_id:
            return account_id
    return None


def handle_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a verified Stripe event to account state.

    All mutations are idempotent sets, so Stripe's at-least-once delivery is
    safe; event ids are additionally recorded so a redelivery is acked
    without re-applying (the stripe_events dedupe table).
    """
    event_id = str(event.get("id") or "")
    etype = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}
    if event_id and accounts_store.stripe_event_seen(event_id):
        return {"handled": False, "type": etype, "reason": "duplicate event"}

    result: Optional[Dict[str, Any]] = None

    if etype == "checkout.session.completed":
        account_id = data.get("client_reference_id") or (data.get("metadata") or {}).get(
            "account_id"
        )
        if not account_id:
            return {"handled": False, "type": etype, "reason": "no account reference"}
        accounts_store.set_subscription(
            account_id,
            "active",
            stripe_customer_id=data.get("customer"),
            stripe_subscription_id=data.get("subscription"),
        )
        result = {"handled": True, "type": etype, "account_id": account_id}

    elif etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        account_id = _account_id_for(data.get("customer"), data.get("metadata"))
        if not account_id:
            return {"handled": False, "type": etype, "reason": "unknown customer"}
        if etype.endswith("deleted"):
            status = "canceled"
        else:
            status = _STATUS_MAP.get(data.get("status", ""), "past_due")
        accounts_store.set_subscription(
            account_id,
            status,
            stripe_customer_id=data.get("customer"),
            stripe_subscription_id=data.get("id"),
        )
        result = {"handled": True, "type": etype, "account_id": account_id, "status": status}

    elif etype in ("invoice.payment_failed", "invoice.paid"):
        account_id = _account_id_for(data.get("customer"), None)
        if not account_id:
            return {"handled": False, "type": etype, "reason": "unknown customer"}
        # A paid invoice recovers the account; a failed one downgrades it.
        status = "active" if etype.endswith("paid") else "past_due"
        accounts_store.set_subscription(account_id, status)
        result = {"handled": True, "type": etype, "account_id": account_id, "status": status}

    if result is not None:
        if event_id:
            accounts_store.record_stripe_event(
                event_id, etype, result.get("account_id")
            )
        return result
    return {"handled": False, "type": etype, "reason": "unhandled event type"}
