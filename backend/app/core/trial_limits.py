"""Server-side trial quotas — the boundary of what a free account can do.

Counters (env-overridable):
- ``generation``    lifetime product generations   (TRIAL_GENERATION_LIMIT, 3)
- ``chat_message``  chat messages per day          (TRIAL_CHAT_MESSAGES_PER_DAY, 100)
- ``export``        lifetime prototype exports     (TRIAL_EXPORT_LIMIT, 5)
- ``draft``         blueprint drafts/plans per day (TRIAL_DRAFT_LIMIT, 20)

Applies to real user accounts without an active subscription. Active
subscribers, admin/master-key, local-dev principals, and the ops smoke
accounts (``factory-smoke-*@cerebrum-dev.invalid``) are exempt. Enforced
here, server-side — never in the UI. The deploy-gate principals must not
burn the public trial cap or the live smoke cannot be re-run.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from . import accounts_store

# The principals the deploy gate's smoke-login issues (routers/accounts.py
# reads these; this is their one definition). The release cycle runs the smoke
# and its repro builds AT THE SAME TIME, each on its own account, so the roster
# is a declared count rather than a fixed pair: index 0 is the smoke's own
# principal, the rest carry the repro builds. Quota exemption keys on "an
# account the smoke gate issued"; the reserved build slot keys on index 0
# ONLY, so a repro build can never take the slot the smoke depends on.
# Five: the smoke plus four builds per cycle -- the two anchors and the two
# rotation picks (scripts/release_cycle.json, backend/tests/repro_pool);
# tests/test_repro_rotation.py fails if the cycle ever needs more.
SMOKE_PRINCIPAL_COUNT = 5
SMOKE_PRINCIPALS = tuple(
    f"factory-smoke-{chr(ord('a') + i)}@cerebrum-dev.invalid"
    for i in range(SMOKE_PRINCIPAL_COUNT)
)
SMOKE_PRINCIPAL_A = SMOKE_PRINCIPALS[0]
SMOKE_PRINCIPAL_B = SMOKE_PRINCIPALS[1]
#: The one principal whose builds may use the worker's reserved slot.
SMOKE_RESERVED_PRINCIPAL = SMOKE_PRINCIPAL_A
OPS_SMOKE_EMAILS = frozenset(SMOKE_PRINCIPALS)

# counter -> (env var, default limit, scope)
TRIAL_COUNTERS: Dict[str, tuple] = {
    "generation": ("TRIAL_GENERATION_LIMIT", 3, "lifetime"),
    "chat_message": ("TRIAL_CHAT_MESSAGES_PER_DAY", 100, "daily"),
    "export": ("TRIAL_EXPORT_LIMIT", 5, "lifetime"),
    # Drafting is an iterative, paid LLM action: daily like chat, not lifetime
    # like generation, so refining a brief cannot burn the generation budget.
    "draft": ("TRIAL_DRAFT_LIMIT", 20, "daily"),
}


class TrialLimitExceeded(HTTPException):
    def __init__(self, counter: str, limit: int, scope: str):
        super().__init__(
            status_code=429,
            detail={
                "error": "trial_limit_reached",
                "counter": counter,
                "limit": limit,
                "scope": scope,
                "message": (
                    f"Free-trial limit reached for {counter} "
                    f"({limit} per {'day' if scope == 'daily' else 'trial'}). "
                    "Subscribe to continue."
                ),
                "upgrade": "/v1/billing/checkout",
            },
        )


def _limit(counter: str) -> tuple[int, str]:
    env, default, scope = TRIAL_COUNTERS[counter]
    try:
        return max(0, int(os.getenv(env, str(default)))), scope
    except ValueError:
        return default, scope


def _period(scope: str) -> str:
    if scope == "daily":
        return datetime.now(timezone.utc).date().isoformat()
    return "lifetime"


def trials_enforced() -> bool:
    """Whether the free-trial quotas bind at all on this deployment.

    A quota says "subscribe to continue". With Stripe unconfigured there is
    nothing to subscribe TO -- checkout and portal answer 503
    ``stripe_not_configured`` and the webhook that would mark an account
    active is inert -- so the cap is a wall with no door: the owner's own
    account runs out of generations and cannot buy more. Quotas therefore
    bind only where billing is actually configured.

    ``TRIAL_LIMITS_ENFORCED`` overrides either way (``1`` to enforce anyway,
    ``0`` to keep them off even once Stripe is live).
    """
    override = (os.getenv("TRIAL_LIMITS_ENFORCED") or "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    try:
        from .stripe_billing import stripe_configured

        return bool(stripe_configured())
    except Exception:  # noqa: BLE001 -- unreadable billing config is not a paywall
        return False


def _stored_email(account_id: Optional[str]) -> str:
    """The account's stored email, lowercased; empty when unknown/unreadable."""
    if not account_id:
        return ""
    try:
        fields = accounts_store.subscription_fields(account_id)
    except Exception:  # noqa: BLE001 -- an unreadable store grants nothing
        return ""
    if fields is None:
        return ""
    return str(fields.get("email") or "").strip().lower()


def is_ops_smoke_account(account_id: Optional[str]) -> bool:
    """Whether ``account_id`` is one of the smoke gate's own principals.

    Resolved server-side from the stored account, never from anything the
    caller sends. Unknown or unreadable accounts are not smoke principals.
    """
    return _stored_email(account_id) in OPS_SMOKE_EMAILS


def is_reserved_smoke_account(account_id: Optional[str]) -> bool:
    """Whether ``account_id`` is the smoke's OWN principal (roster index 0).

    Only this account's builds may use the worker's reserved slot. The other
    smoke-issued principals carry repro builds that run beside the smoke; they
    queue like any user so they can never take the slot the smoke needs.
    """
    return _stored_email(account_id) == SMOKE_RESERVED_PRINCIPAL


def _is_limited_account(account_id: Optional[str]) -> bool:
    """Quotas bind real accounts without an active subscription only."""
    if not account_id:
        return False
    if not trials_enforced():
        return False
    fields = accounts_store.subscription_fields(account_id)
    if fields is None:
        return False
    email = str(fields.get("email") or "").strip().lower()
    if email in OPS_SMOKE_EMAILS:
        return False
    return (fields.get("subscription_status") or "none").strip().lower() != "active"


def consume(account_id: str, counter: str) -> Dict[str, Any]:
    """Spend one unit of ``counter``; report whether it was within the limit."""
    limit, scope = _limit(counter)
    value = accounts_store.increment_usage(account_id, counter, _period(scope))
    return {
        "allowed": value <= limit,
        "used": value,
        "limit": limit,
        "remaining": max(0, limit - value),
        "scope": scope,
    }


def refund(account_id: Optional[str], counter: str) -> None:
    """Return one unit of ``counter`` after a failed generation attempt."""
    if not account_id or not _is_limited_account(account_id):
        return
    _limit_unused, scope = _limit(counter)
    accounts_store.decrement_usage(account_id, counter, _period(scope))


def remaining_ok(account_id: Optional[str], counter: str) -> bool:
    """Read-only check: True when another unit would still be within the limit."""
    if not _is_limited_account(account_id):
        return True
    assert account_id is not None
    limit, scope = _limit(counter)
    used = accounts_store.get_usage(account_id, counter, _period(scope))
    return used < limit


def require_remaining(account_id: Optional[str], counter: str) -> None:
    """429 if the next consume would exceed the limit. Does not increment."""
    if remaining_ok(account_id, counter):
        return
    limit, scope = _limit(counter)
    raise TrialLimitExceeded(counter, limit, scope)


def require_within_limit(account_id: Optional[str], counter: str) -> None:
    """Server-side quota gate. Raises 429 TrialLimitExceeded over the limit."""
    if not _is_limited_account(account_id):
        return
    outcome = consume(account_id, counter)
    if not outcome["allowed"]:
        limit, scope = _limit(counter)
        raise TrialLimitExceeded(counter, limit, scope)


def quota_snapshot(account_id: str) -> Dict[str, Dict[str, Any]]:
    """Read-only usage snapshot for the account (no increments)."""
    snapshot: Dict[str, Dict[str, Any]] = {}
    for counter in TRIAL_COUNTERS:
        limit, scope = _limit(counter)
        used = accounts_store.get_usage(account_id, counter, _period(scope))
        snapshot[counter] = {
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "scope": scope,
        }
    return snapshot
