"""Billing & trial entitlement (P3).

Model: every new account gets a ``TRIAL_DAYS``-day free trial, no card upfront.
When the trial ends, the account is blocked from paid actions (creating
sessions, running the factory) until the subscription becomes ``active`` —
the Stripe checkout/webhook slice flips that via ``set_subscription``.

Enforcement is opt-in (``BILLING_ENFORCEMENT``) so existing deployments are
unaffected until the switch is flipped at launch. Admin (master key) and dev
principals always pass — billing only governs real user accounts.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException

from . import accounts_store
from .auth import Principal, require_api_key

_TRUTHY = {"1", "true", "yes", "on"}


def enforcement_enabled() -> bool:
    return os.getenv("BILLING_ENFORCEMENT", "").strip().lower() in _TRUTHY


def _parse(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def is_entitled(subscription_status: Optional[str], trial_ends_at: Optional[str]) -> bool:
    """Active subscription always passes; a trial passes while it is live.

    Legacy accounts (no billing fields, status ``none``) are NOT entitled —
    when enforcement is flipped on, grant them explicitly via
    ``accounts_store.set_subscription``.
    """
    status = (subscription_status or "none").strip().lower()
    if status == "active":
        return True
    if status == "trialing":
        ends = _parse(trial_ends_at)
        if ends is None:
            return False
        return ends > datetime.now(timezone.utc)
    return False


def billing_status(account_id: str) -> Optional[Dict[str, Any]]:
    """Full billing snapshot for the status endpoint / register response."""
    fields = accounts_store.subscription_fields(account_id)
    if fields is None:
        return None
    status = fields["subscription_status"] or "none"
    ends_raw = fields["trial_ends_at"]
    ends = _parse(ends_raw)
    days_left: Optional[int] = None
    if ends is not None:
        remaining = (ends - datetime.now(timezone.utc)).total_seconds()
        days_left = max(0, math.ceil(remaining / 86400))
    return {
        "account_id": account_id,
        "subscription_status": status,
        "trial_ends_at": ends_raw,
        "trial_days_left": days_left,
        "trial_days": accounts_store.trial_days(),
        "entitled": is_entitled(status, ends_raw),
        "enforcement": enforcement_enabled(),
    }


def assert_entitled(principal: Principal) -> Principal:
    """Same gate as :func:`require_entitled`, callable from handlers and streams.

    Raises 402 ``trial_expired`` so the frontend can route to the billing page
    (distinct from 401 bad credential / 403 not-verified).
    """
    if not enforcement_enabled():
        return principal
    if principal.kind != "user" or not principal.account_id:
        return principal
    fields = accounts_store.subscription_fields(principal.account_id)
    if fields is None:
        raise HTTPException(status_code=403, detail="account_not_found")
    if not is_entitled(fields["subscription_status"], fields["trial_ends_at"]):
        raise HTTPException(status_code=402, detail="trial_expired")
    return principal


def require_entitled(principal: Principal = Depends(require_api_key)) -> Principal:
    """Gate paid actions: admin/dev pass; users need an active sub or live trial."""
    return assert_entitled(principal)
