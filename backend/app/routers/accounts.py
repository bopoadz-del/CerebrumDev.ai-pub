"""User accounts: register, login, email verification, per-user API keys,
plus the data-rights endpoints (export and erasure)."""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import accounts_store, billing, data_rights, mailer
from ..core.auth import Principal, require_account_allow_unverified, require_api_key
from ..core.auth_cookies import clear_login_cookie, cookie_login_token, set_login_cookie
from ..core.rate_limit import check_rate_limit_for_request
from ..core.trial_limits import SMOKE_PRINCIPALS

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8
# Ops-only principals for production smoke. The .invalid TLD cannot receive
# mail; public register never uses these addresses.
#: How many principals smoke-login issues when the caller does not ask: the
#: smoke's own plus the second one its isolation check reads with.
SMOKE_LOGIN_DEFAULT_PRINCIPALS = 2


class RegisterBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=MIN_PASSWORD_LEN, max_length=256)


class LoginBody(BaseModel):
    email: str
    password: str


class VerifyBody(BaseModel):
    token: str


class KeyBody(BaseModel):
    label: str = ""


class ForgotBody(BaseModel):
    email: str


class DeleteAccountBody(BaseModel):
    """Re-authentication for erasure. A bearer token alone is not enough."""

    password: str = Field(..., min_length=1, max_length=256)


class ResetBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LEN, max_length=256)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LEN, max_length=256)


def _smoke_gate_token() -> str:
    return os.getenv("SMOKE_GATE_TOKEN", "").strip()


def _smoke_gate_matches(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def _smoke_account_password(gate: str) -> str:
    explicit = os.getenv("SMOKE_ACCOUNT_PASSWORD", "").strip()
    if explicit:
        return explicit
    return "smk_" + hmac.new(
        b"cerebrumdev-smoke-account", gate.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def _dev_tokens_exposed() -> bool:
    """Whether verification/reset tokens may be returned in an HTTP response.

    Off unless explicitly enabled. These endpoints are public and
    unauthenticated, so returning a live ``cdr_`` reset token to the caller is
    an account takeover for anyone who knows a victim's email address.

    The old condition was "mail delivery returned False", which fails open in
    two ways: mail is unconfigured until an operator fills in the credentials
    (they ship as ``sync: false``), and ``mailer`` swallows every exception and
    reports False -- so a provider outage, a 429, or a rotated key silently
    reopens it on a correctly configured deployment. Convenience during local
    development is not worth a control that switches itself off during an
    incident.
    """
    return os.getenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _rate_limit(request: Request, bucket: str) -> None:
    # Identity comes from the socket peer unless TRUSTED_PROXY explicitly says
    # otherwise. Never trust X-Forwarded-For by default: that would make the
    # limit key caller-controlled and the throttle trivially bypassable.
    if not check_rate_limit_for_request(bucket, request):
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": "600"},
        )


def _require_user(principal: Principal) -> Principal:
    if principal.kind != "user" or not principal.account_id:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires an account credential (login token or API key)",
        )
    return principal


def _json_with_login_cookie(
    request: Request, payload: dict, status_code: int = 200
) -> JSONResponse:
    """Return JSON and set the HttpOnly ``cdt`` cookie for browser clients.

    ``login_token`` stays in the body so smoke scripts and ``cdk_``-style
    API clients keep working. The Floor SPA ignores the body token and
    sends the cookie with ``credentials: include``.
    """
    response = JSONResponse(payload, status_code=status_code)
    token = payload.get("login_token")
    if isinstance(token, str):
        set_login_cookie(response, token, request)
    return response


@router.post("/register", status_code=201)
async def register(body: RegisterBody, request: Request):
    _rate_limit(request, "register")
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    try:
        account = accounts_store.create_account(email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Email already registered") from exc

    verify_token = accounts_store.issue_verify_token(account["account_id"])
    sent = mailer.send_verification_email(email, verify_token)
    login_token = accounts_store.issue_login_token(account["account_id"])

    if sent:
        verification = {"mode": "smtp", "email_sent": True}
    elif _dev_tokens_exposed():
        verification = {
            "mode": "dev_token",
            "email_sent": False,
            "note": "SMTP not configured — verify via POST /v1/auth/verify-email with this token",
            "dev_verification_token": verify_token,
        }
    elif not mailer.email_configured():
        # No provider at all. "Request a new one shortly" here was a lie --
        # retrying can never succeed until an operator configures
        # RESEND_API_KEY or SMTP_*, and telling users to retry dead-ends
        # them. Say what is actually true.
        verification = {
            "mode": "unconfigured",
            "email_sent": False,
            "note": (
                "Email verification is not yet enabled on this deployment. "
                "Your account works without it; verification will be "
                "requested by email once enabled."
            ),
        }
    else:
        verification = {
            "mode": "unavailable",
            "email_sent": False,
            "note": "Verification email could not be sent. Request a new one shortly.",
        }
    return _json_with_login_cookie(
        request,
        {
            "ok": True,
            "account_id": account["account_id"],
            "email": account["email"],
            "email_verified": False,
            "login_token": login_token,
            "verification": verification,
            "billing": billing.billing_status(account["account_id"]),
            "what_this_is_not_yet": (
                "The factory generates a working prototype — real code, tests and "
                "deploy files — not a finished production system. Third-party "
                "integrations in generated products are stubs until you connect "
                "your own credentials, deployment is a step you run rather than "
                "something that happens for you, and free-trial accounts have "
                "server-enforced caps on generations, daily chat and exports. "
                "Answers are grounding-checked: when a claim can't be verified it "
                "is withheld, not invented."
            ),
        },
        status_code=201,
    )


@router.post("/login")
async def login(body: LoginBody, request: Request):
    _rate_limit(request, "login")
    account = accounts_store.authenticate(body.email, body.password)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _json_with_login_cookie(
        request,
        {
            "ok": True,
            "account_id": account["account_id"],
            "email": account["email"],
            "email_verified": account["email_verified"],
            "login_token": accounts_store.issue_login_token(account["account_id"]),
        },
    )


@router.post("/logout")
async def logout(request: Request):
    """Clear the HttpOnly session cookie. Safe to call while unauthenticated."""
    response = JSONResponse({"ok": True})
    clear_login_cookie(response, request)
    return response


@router.get("/me")
async def me(principal: Principal = Depends(require_api_key)):
    # 401 (not 403): the Floor SPA boots Sign in on 401. With
    # ALLOW_ANONYMOUS_DEV the dependency returns a `dev` principal, and a
    # 403 here was painted as "Factory unreachable" instead of the auth form.
    if principal.kind != "user" or not principal.account_id:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    account = accounts_store.get_account(principal.account_id or "")
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, **account, "auth_kind": principal.kind}


@router.post("/smoke-login")
async def smoke_login(request: Request):
    """Issue verified smoke principals. Fail-closed without SMOKE_GATE_TOKEN.

    Public email verification is unchanged. This path exists so the deploy
    gate can create a session with a verified test principal (or two, for
    isolation) without disabling ``ACCOUNTS_REQUIRE_VERIFIED_EMAIL``.

    The release cycle runs repro builds BESIDE the smoke, each on its own
    account, so the caller may ask for ``{"principals": n}`` (1..the declared
    roster size). Index 0 is always the smoke's own principal. Asking for more
    than the roster holds is refused, never silently trimmed: a cycle that
    planned three accounts and received two would put two builds on one.
    """
    expected = _smoke_gate_token()
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    provided = (request.headers.get("X-Smoke-Gate") or "").strip()
    if not _smoke_gate_matches(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing smoke gate token")
    _rate_limit(request, "smoke-login")
    count = SMOKE_LOGIN_DEFAULT_PRINCIPALS
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- no body asks for the default pair
        body = None
    if isinstance(body, dict) and "principals" in body:
        asked = body.get("principals")
        if (
            not isinstance(asked, int)
            or isinstance(asked, bool)
            or not 1 <= asked <= len(SMOKE_PRINCIPALS)
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"principals must be an integer 1..{len(SMOKE_PRINCIPALS)} "
                    f"(the declared smoke roster); got {asked!r}"
                ),
            )
        count = asked
    password = _smoke_account_password(expected)
    accounts = [
        accounts_store.ensure_verified_account(email, password)
        for email in SMOKE_PRINCIPALS[:count]
    ]
    tokens = [accounts_store.issue_login_token(a["account_id"]) for a in accounts]
    payload = {
        "ok": True,
        "email_verified": True,
        "login_token": tokens[0],
        "account_id": accounts[0]["account_id"],
        "login_tokens": tokens,
        "account_ids": [a["account_id"] for a in accounts],
    }
    if count >= 2:
        payload["login_token_b"] = tokens[1]
        payload["account_id_b"] = accounts[1]["account_id"]
    return _json_with_login_cookie(request, payload)


@router.get("/smoke-process-isolation")
async def smoke_process_isolation(request: Request):
    """Post-deploy guard: no live coding agent shares the server's session.

    Same gate as smoke-login (404 when unconfigured, 401 without the token):
    the answer is counts and booleans only -- never a pid or session id.
    """
    expected = _smoke_gate_token()
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    provided = (request.headers.get("X-Smoke-Gate") or "").strip()
    if not _smoke_gate_matches(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing smoke gate token")
    import asyncio

    from ..factory.build.agent_process import process_isolation_report

    # The report runs a short group-kill probe (a child process); keep it off
    # the event loop.
    return {"ok": True, **(await asyncio.to_thread(process_isolation_report))}


@router.post("/verify-email")
async def verify_email(body: VerifyBody, request: Request):
    _rate_limit(request, "verify-email")
    account_id = accounts_store.confirm_verify_token(body.token.strip())
    if account_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    return {"ok": True, "account_id": account_id, "email_verified": True}


@router.post("/forgot-password")
async def forgot_password(body: ForgotBody, request: Request):
    """Issue a reset token. Response never reveals whether the email exists —
    except in dev mode (no SMTP), where the token is surfaced for the owner."""
    _rate_limit(request, "forgot-password")
    email = body.email.strip().lower()
    token = accounts_store.issue_reset_token(email)
    sent = mailer.send_password_reset_email(email, token) if token else False
    resp: dict = {
        "ok": True,
        "message": "If the email is registered, a reset link follows.",
    }
    if token and not sent and _dev_tokens_exposed():
        resp["note"] = "SMTP not configured — reset via POST /v1/auth/reset-password with this token"
        resp["dev_reset_token"] = token
    return resp


@router.post("/reset-password")
async def reset_password(body: ResetBody, request: Request):
    _rate_limit(request, "reset-password")
    account_id = accounts_store.confirm_reset_token(body.token.strip(), body.new_password)
    if account_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    response = JSONResponse(
        {
            "ok": True,
            "account_id": account_id,
            "message": "Password updated — sign in again (all previous sessions were closed).",
        }
    )
    clear_login_cookie(response, request)
    return response


def _request_login_token(request: Request) -> str:
    """The credential the caller presented (Bearer, X-API-Key, then cookie).

    Whether it is a login session is decided by the store, which keeps the
    session whose hash it holds -- never by the token's spelling. An API key
    or the master key here keeps nothing: it matches no login-session row.
    """
    authorization = (request.headers.get("Authorization") or "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    x_key = (request.headers.get("X-API-Key") or "").strip()
    if x_key:
        return x_key
    return cookie_login_token(request)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    request: Request,
    principal: Principal = Depends(require_account_allow_unverified),
):
    """Signed-in password change. Verifies the current password, updates the
    hash, and revokes every other login session (this session stays).

    Uses the unverified-allowed dependency so an account that has not
    finished email verify can still set a password (same as resend).
    """
    _rate_limit(request, "change-password")
    account_id = principal.account_id or ""
    if not accounts_store.verify_account_password(account_id, body.current_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    if not accounts_store.change_account_password(
        account_id,
        body.new_password,
        keep_login_token=_request_login_token(request),
    ):
        raise HTTPException(status_code=404, detail="Account not found")
    return {
        "ok": True,
        "account_id": account_id,
        "message": "Password updated. Other sessions were signed out.",
    }


@router.post("/keys", status_code=201)
async def create_key(body: KeyBody, principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    issued = accounts_store.issue_api_key(principal.account_id or "", body.label)
    return {
        "ok": True,
        "key_id": issued["key_id"],
        "api_key": issued["api_key"],
        "note": "Store this key now — it is shown once and only its hash is kept.",
    }


@router.get("/keys")
async def list_keys(principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    return {"ok": True, "keys": accounts_store.list_api_keys(principal.account_id or "")}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, principal: Principal = Depends(require_api_key)):
    _require_user(principal)
    if not accounts_store.revoke_api_key(principal.account_id or "", key_id):
        raise HTTPException(status_code=404, detail="Key not found or already revoked")
    return {"ok": True, "key_id": key_id, "revoked": True}


@router.get("/export")
async def export_my_data(principal: Principal = Depends(require_api_key)):
    """Everything the platform holds about the calling account, as one JSON
    document: profile, billing identifiers, usage counters, owned session ids
    and API key metadata.

    Credential material is excluded by construction — see
    ``accounts_store.export_account``. Exporting a password hash or a token
    hash would hand an attacker with a single stolen bearer token an offline
    cracking target and a permanent equality oracle.
    """
    _require_user(principal)
    payload = data_rights.export_account(principal.account_id or "")
    if payload is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True, "account_id": principal.account_id, **payload}


@router.delete("/account", status_code=204)
async def delete_my_account(
    body: DeleteAccountBody,
    request: Request,
    principal: Principal = Depends(require_api_key),
):
    """Erase the calling account and everything owned by it.

    Re-authentication is mandatory: the caller must supply the account's
    current password in the body. A bearer token or API key identifies a
    *client*, and clients get stolen, cached and left in browser history.
    Irreversible destruction of every session, upload and vector index the user
    owns is not something a leaked token should be able to do on its own.

    Responses:
    * ``204`` — everything was purged.
    * ``200`` — the account row is gone but at least one content category could
      not be purged. The body names the failing categories and the session ids
      that still need manual attention. A partial purge reported as success is
      worse than an honest partial.
    * ``403`` — wrong password; nothing is touched.
    """
    _require_user(principal)
    _rate_limit(request, "delete-account")
    account_id = principal.account_id or ""
    if not accounts_store.verify_account_password(account_id, body.password):
        raise HTTPException(
            status_code=403,
            detail="Current password is required to delete this account",
        )

    report = data_rights.purge_account(account_id)
    if report["ok"]:
        response = Response(status_code=204)
        clear_login_cookie(response, request)
        return response
    response = JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "message": (
                "Account erased, but some stored content could not be removed. "
                "Contact support with this report."
            ),
            **report,
        },
    )
    clear_login_cookie(response, request)
    return response


@router.post("/admin/retention")
async def run_retention(principal: Principal = Depends(require_api_key)):
    """Purge expired login tokens and expired verification/reset token hashes.

    Master-key only, and intentionally pull-based: no scheduler, no background
    thread. Point a cron job or an ops one-liner at it.
    """
    if principal.kind != "admin":
        raise HTTPException(status_code=403, detail="Master key required")
    return data_rights.run_retention_pass()
