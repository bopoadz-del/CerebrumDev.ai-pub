"""Resend email verification for an unverified login token.

Credential-gated routes 403 ``email_not_verified``, so this path authenticates
without that check. Register still issues the first token; this reissues.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import accounts_store, mailer
from ..core.auth import Principal, require_account_allow_unverified
from ..core.rate_limit import check_rate_limit_for_request

router = APIRouter()


def _dev_tokens_exposed() -> bool:
    return os.getenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _verification_payload(*, sent: bool, verify_token: str) -> dict:
    if sent:
        return {"mode": "smtp", "email_sent": True}
    if _dev_tokens_exposed():
        return {
            "mode": "dev_token",
            "email_sent": False,
            "note": "SMTP not configured — verify via POST /v1/auth/verify-email with this token",
            "dev_verification_token": verify_token,
        }
    if not mailer.email_configured():
        return {
            "mode": "unconfigured",
            "email_sent": False,
            "note": (
                "Email verification is not yet enabled on this deployment. "
                "Your account works without it; verification will be "
                "requested by email once enabled."
            ),
        }
    return {
        "mode": "unavailable",
        "email_sent": False,
        "note": "Verification email could not be sent. Request a new one shortly.",
    }


def _rate_limit(request: Request, bucket: str) -> None:
    if not check_rate_limit_for_request(bucket, request):
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": "600"},
        )


@router.post("/resend-verification")
async def resend_verification(
    request: Request,
    principal: Principal = Depends(require_account_allow_unverified),
):
    """Issue a new verification mail (or a ``dev_token``) for the caller."""
    _rate_limit(request, "resend-verification")
    account = accounts_store.get_account(principal.account_id or "")
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if account["email_verified"]:
        return {"ok": True, "already_verified": True, "email_verified": True}
    verify_token = accounts_store.issue_verify_token(account["account_id"])
    sent = mailer.send_verification_email(account["email"], verify_token)
    return {
        "ok": True,
        "already_verified": False,
        "email": account["email"],
        "email_verified": False,
        "verification": _verification_payload(sent=sent, verify_token=verify_token),
    }
