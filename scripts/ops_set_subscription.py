#!/usr/bin/env python3
"""Set an account's subscription state without going through Stripe.

WHY THIS EXISTS
---------------
``core/billing.py`` line 46 prescribes the remedy for a locked-out account:
"grant them explicitly via ``accounts_store.set_subscription``". Repo-wide,
that function had exactly one caller -- the Stripe webhook. So with
``BILLING_ENFORCEMENT`` on and Stripe unconfigured, the documented remedy was
not reachable: ``require_entitled`` returns 402, ``POST /v1/billing/checkout``
returns 503 ``stripe_not_configured``, and recovery meant editing the accounts
database by hand.

This is that seam, and it deliberately calls the same function the webhook
calls rather than writing rows itself -- so ops and Stripe cannot drift into
setting subscription state two different ways.

Run on the deployment host, where the accounts DB env is present:

    python scripts/ops_set_subscription.py <account_id> active
    python scripts/ops_set_subscription.py <account_id> canceled --customer cus_x

Status values are the four ``accounts_store`` documents at line 353:
trialing / active / past_due / canceled.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import accounts_store  # noqa: E402

STATUSES = ("trialing", "active", "past_due", "canceled")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set subscription state via the same seam the Stripe webhook uses."
    )
    parser.add_argument("account_id")
    parser.add_argument("status", choices=STATUSES)
    parser.add_argument("--customer", default=None, help="stripe_customer_id")
    parser.add_argument("--subscription", default=None, help="stripe_subscription_id")
    args = parser.parse_args()

    changed = accounts_store.set_subscription(
        args.account_id,
        args.status,
        stripe_customer_id=args.customer,
        stripe_subscription_id=args.subscription,
    )
    if not changed:
        # set_subscription returns rowcount > 0, so a False here means no row
        # matched -- the account id is wrong. Say that rather than "failed".
        print(f"no account matched {args.account_id!r}; nothing was changed")
        return 1
    print(f"{args.account_id} -> {args.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
