"""Auth for CerebrumDev.ai backend — master key + per-user credentials.

Resolution order for every request:
1. ``CEREBRUM_DEV_API_KEY`` (master/admin key) — full access, header only.
2. Per-account API key (``cdk_…``) via Bearer or X-API-Key.
3. Per-account login token (``cdt_…``) via Bearer, then the HttpOnly ``cdt`` cookie.
4. No master key configured AND ``ALLOW_ANONYMOUS_DEV=1`` → open dev principal.

The anonymous dev principal is opt-in. It used to be the fallback whenever no
master key was configured, gated only by ``ENV == "production"`` matching that
exact string. That failed open: an unset or misspelled ``ENV``, or a typo in
the ``CEREBRUM_DEV_API_KEY`` variable name, silently granted every caller a
``dev`` principal. Because ownership checks throughout the codebase are written
as ``if principal.kind == "user"``, a ``dev`` principal bypasses every one of
them and reads all tenants' sessions, products and uploads, with billing and
trial quotas disabled. One missing environment variable must not do that, so
open mode now requires deliberate opt-in and the process refuses to boot
without either a master key or that opt-in.

Set ``ACCOUNTS_REQUIRE_VERIFIED_EMAIL=1`` to block unverified accounts from
all credential-gated routes (403 ``email_not_verified``). Verification and
password-reset endpoints stay public so users can always complete the flow.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import Header, HTTPException, Request

from .auth_cookies import cookie_login_token

_TRUTHY = {"1", "true", "yes", "on"}


def _master_key() -> str:
    """Read the master key per call.

    Deliberately not cached at import time: a module-level snapshot is taken
    before the process finishes loading its environment, so a key set slightly
    later reads as absent and silently downgrades auth.
    """
    return os.getenv("CEREBRUM_DEV_API_KEY", "").strip()


def _anonymous_dev_allowed() -> bool:
    """Whether the credential-free ``dev`` principal may be issued."""
    return os.getenv("ALLOW_ANONYMOUS_DEV", "").strip().lower() in _TRUTHY


def _env_name() -> str:
    return os.getenv("ENV", "").strip().lower()


def _env_is_production() -> bool:
    return _env_name() in {"production", "prod"}


@dataclass(frozen=True)
class Principal:
    """Who is calling. ``kind``: admin (master key) | user (account) | dev (open)."""

    kind: str
    account_id: Optional[str] = None
    email: Optional[str] = None
    email_verified: Optional[bool] = None


def verify_production_auth() -> None:
    """Refuse to boot in any configuration that would accept anonymous callers.

    Not conditional on ``ENV`` for the master-key requirement: ``prod``,
    ``PRODUCTION``, or an unset value must not boot wide open. In addition,
    ``ALLOW_ANONYMOUS_DEV`` is refused when ``ENV`` is ``production``/``prod``
    even if a master key exists — the flag bypasses ownership checks and
    must never ride along in a shared environment.
    """
    if _env_is_production() and _anonymous_dev_allowed():
        raise RuntimeError(
            "Refusing to start: ALLOW_ANONYMOUS_DEV is set while ENV is "
            "production/prod. The anonymous 'dev' principal bypasses all "
            "per-account ownership checks. Unset ALLOW_ANONYMOUS_DEV in any "
            "shared environment."
        )
    if _master_key():
        return
    if _anonymous_dev_allowed():
        return
    raise RuntimeError(
        "Refusing to start: no CEREBRUM_DEV_API_KEY is configured and "
        "ALLOW_ANONYMOUS_DEV is not set. Without one of these every request "
        "would be granted an anonymous 'dev' principal, which bypasses all "
        "per-account ownership checks. Set CEREBRUM_DEV_API_KEY in any shared "
        "environment, or ALLOW_ANONYMOUS_DEV=1 for a local sandbox."
    )


def _header_token(authorization: Optional[str], x_api_key: Optional[str]) -> str:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = token.strip()
            if token:
                return token
    if x_api_key:
        return x_api_key.strip()
    return ""


def _provided_credential(
    authorization: Optional[str],
    x_api_key: Optional[str],
    request: Optional[Request] = None,
) -> Tuple[str, bool]:
    """``(token, from_cookie)``. Header credentials win.

    The cookie only ever holds what :func:`set_login_cookie` stored -- a login
    session the store minted -- so a cookie credential resolves as a login
    session and nothing else: never the master key, never an API key. Where it
    came from is the type; its spelling is never read.
    """
    header = _header_token(authorization, x_api_key)
    if header:
        return header, False
    return cookie_login_token(request), True


def _verification_required() -> bool:
    return os.getenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_user_principal(token: str, *, login_only: bool = False) -> Optional[Principal]:
    """The account a credential belongs to, by lookup in the store's typed
    tables (API keys, then login sessions) -- the store knows what it minted."""
    from . import accounts_store

    account = None if login_only else accounts_store.account_for_api_key(token)
    if account is None:
        account = accounts_store.account_for_login_token(token)
    if account is None:
        return None
    return Principal(
        kind="user",
        account_id=account["account_id"],
        email=account["email"],
        email_verified=account["email_verified"],
    )


def _enforce_verification(principal: Principal) -> Principal:
    if _verification_required() and principal.email_verified is False:
        raise HTTPException(status_code=403, detail="email_not_verified")
    return principal


def _tokens_match(provided: str, expected: str) -> bool:
    """Constant-time equality that does not 500 on length mismatch.

    ``hmac.compare_digest`` on the raw strings raises ``ValueError`` on
    Python < 3.12 when the lengths differ. Production is 3.11. Without the
    hash, a login token (``cdt_…``) compared against the master key would
    crash the request before user-principal resolution — every signed-in
    caller 500ing on every credential-gated route.

    Hashing both sides first makes the compared buffers fixed-length, which
    is also what :func:`require_master_key` already did.
    """
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def require_master_key(
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    """Admin-only. User tokens and anonymous dev cannot trigger ops actions.

    Fail-closed when ``CEREBRUM_DEV_API_KEY`` is unset (404) so the route
    does not exist as an unauthenticated backup trigger.
    """
    master = _master_key()
    if not master:
        raise HTTPException(status_code=404, detail="Not Found")
    provided = _header_token(authorization, x_api_key)
    if not _tokens_match(provided, master):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return Principal(kind="admin")


def require_api_key(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    """Dependency: master key, per-account credential, or dev-open.

    Returns the resolved :class:`Principal`; routers that need identity
    depend on this same function (FastAPI dedupes it per request).
    """
    provided, from_cookie = _provided_credential(authorization, x_api_key, request)
    master = _master_key()

    if master:
        if provided and not from_cookie and _tokens_match(provided, master):
            return Principal(kind="admin")
        principal = (
            _resolve_user_principal(provided, login_only=from_cookie) if provided else None
        )
        if principal is not None:
            return _enforce_verification(principal)
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # No master key configured. Account credentials still work.
    if provided:
        principal = _resolve_user_principal(provided, login_only=from_cookie)
        if principal is not None:
            return _enforce_verification(principal)

    # Anonymous access is opt-in only. Falling through to a `dev` principal
    # here is what turns a missing environment variable into cross-tenant
    # exposure, because ownership checks elsewhere are written as
    # `principal.kind == "user"` and a `dev` principal skips all of them.
    if _anonymous_dev_allowed():
        return Principal(kind="dev")
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def require_account_allow_unverified(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    """Authenticate a user credential without requiring a verified email.

    ``require_api_key`` 403s unverified callers when
    ``ACCOUNTS_REQUIRE_VERIFIED_EMAIL`` is on. Resend-verification has to
    keep working in that state so the user can finish the flow instead of
    seeing a dead Factory.
    """
    provided, from_cookie = _provided_credential(authorization, x_api_key, request)
    if not provided:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    principal = _resolve_user_principal(provided, login_only=from_cookie)
    if principal is None or principal.kind != "user" or not principal.account_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
        )
    return principal
