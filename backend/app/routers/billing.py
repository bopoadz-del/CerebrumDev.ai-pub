"""Billing: subscription/trial status, Stripe checkout + portal + webhook.

The webhook is public (no account credential) — Stripe's signature
verification is the gate. Everything else requires a user principal.
"""

from __future__ import annotations

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import accounts_store, billing, stripe_billing
from ..core.auth import Principal, require_api_key
from ..core.trial_limits import quota_snapshot

router = APIRouter()


def _require_user(principal: Principal) -> Principal:
    if principal.kind != "user" or not principal.account_id:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires an account credential (login token or API key)",
        )
    return principal


@router.get("/status")
async def status(principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    snap = billing.billing_status(principal.account_id or "")
    if snap is None:
        raise HTTPException(status_code=404, detail="Account not found")
    snap["checkout_available"] = stripe_billing.stripe_configured()
    snap["quotas"] = quota_snapshot(principal.account_id or "")
    return {"ok": True, "billing": snap}


@router.post("/checkout")
async def checkout(principal: Principal = Depends(require_api_key)):
    """Start a Stripe subscription checkout; returns the hosted page URL."""
    _require_user(principal)
    if not stripe_billing.stripe_configured():
        raise HTTPException(
            status_code=503,
            detail="stripe_not_configured",
        )
    account = accounts_store.get_account(principal.account_id or "")
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    url = stripe_billing.create_checkout_session(account["account_id"], account["email"])
    return {"ok": True, "checkout_url": url}


@router.post("/portal")
async def portal(principal: Principal = Depends(require_api_key)):
    """Stripe customer portal (update card, cancel, download invoices)."""
    _require_user(principal)
    if not stripe_billing.stripe_configured():
        raise HTTPException(
            status_code=503,
            detail="stripe_not_configured",
        )
    url = stripe_billing.create_portal_session(principal.account_id or "")
    if url is None:
        raise HTTPException(
            status_code=409,
            detail="no_billing_customer",
        )
    return {"ok": True, "portal_url": url}


@router.post("/webhook")
async def webhook(request: Request):
    """Stripe event sink. Public — the signature check is the auth.

    Always 200s well-formed events (even unhandled types) so Stripe stops
    retrying; 400 on a bad signature; 503 until STRIPE_WEBHOOK_SECRET is set.
    """
    if not stripe_billing.webhook_secret():
        raise HTTPException(status_code=503, detail="stripe_not_configured")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe_billing.construct_webhook_event(payload, signature)
    except stripe.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="invalid_signature") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_payload") from exc
    result = stripe_billing.handle_event(event)
    return {"ok": True, **result}
