"""HttpOnly session cookie for browser login tokens.

The Floor SPA authenticates with a ``cdt`` cookie (Secure + HttpOnly).
API clients keep sending ``Authorization: Bearer cdt_…`` or the master
key / ``cdk_`` header — those paths are unchanged.

Production Floor hosts (``www.cerebrum-dev.com`` / ``cerebrum-dev.com``)
call ``api.cerebrum-dev.com`` cross-origin with ``credentials: include``.
Browsers that treat that fetch as a cross-site context will not *send*
(and in some configurations will not *store*) ``SameSite=Lax`` cookies
from the API host. ``SameSite=None; Secure`` is required so the login
cookie sticks and ``/v1/auth/me`` succeeds after sign-in.

Local HTTP TestClient keeps ``SameSite=Lax`` (browsers reject
``SameSite=None`` without ``Secure``). Override with
``AUTH_COOKIE_SAMESITE=lax|strict|none`` if needed.

Host-only cookies (no ``Domain``) remain the default: the browser stores
the cookie for ``api.cerebrum-dev.com`` and sends it only there.
``AUTH_COOKIE_DOMAIN`` exists for operators who need ``.cerebrum-dev.com``;
it is not required for the www/api pair and is not set on Render from
this change.
"""

from __future__ import annotations

import os
from typing import Optional

from datetime import timedelta

from fastapi import Request
from fastapi.responses import Response

# Must match accounts_store login-token TTL (7d). Cookie max-age is the
# browser-side bound; the hashed row still expires independently.
_LOGIN_COOKIE_TTL = timedelta(days=7)

LOGIN_COOKIE_NAME = "cdt"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_SAMESITE_VALUES = {"lax", "strict", "none"}


def cookie_domain() -> Optional[str]:
    raw = os.getenv("AUTH_COOKIE_DOMAIN", "").strip()
    return raw or None


def cookie_secure(request: Optional[Request] = None) -> bool:
    """Secure in production, or when the request itself is HTTPS.

    TestClient is HTTP, so ENV=test leaves Secure off unless
    ``AUTH_COOKIE_SECURE`` is set. Production (``ENV=production``/``prod``)
    always sets Secure.
    """
    explicit = os.getenv("AUTH_COOKIE_SECURE", "").strip().lower()
    if explicit in _TRUTHY:
        return True
    if explicit in _FALSY:
        return False
    env = os.getenv("ENV", "").strip().lower()
    if env in {"production", "prod"}:
        return True
    if request is not None and request.url.scheme == "https":
        return True
    return False


def cookie_samesite(request: Optional[Request] = None) -> str:
    """SameSite for the login cookie.

    Secure cookies default to ``none`` so www/apex → api credentialed
    fetches keep the session. Non-secure (local TestClient) stays ``lax``.
    """
    explicit = os.getenv("AUTH_COOKIE_SAMESITE", "").strip().lower()
    if explicit in _SAMESITE_VALUES:
        return explicit
    if cookie_secure(request):
        return "none"
    return "lax"


def cookie_max_age() -> int:
    return int(_LOGIN_COOKIE_TTL.total_seconds())


def _cookie_kwargs(request: Optional[Request] = None) -> dict:
    secure = cookie_secure(request)
    samesite = cookie_samesite(request)
    # Browsers reject SameSite=None without Secure; never emit that pair.
    if samesite == "none" and not secure:
        samesite = "lax"
    kwargs: dict = {
        "key": LOGIN_COOKIE_NAME,
        "httponly": True,
        "samesite": samesite,
        "secure": secure,
        "path": "/",
        "max_age": cookie_max_age(),
    }
    domain = cookie_domain()
    if domain:
        kwargs["domain"] = domain
    return kwargs


def set_login_cookie(response: Response, token: str, request: Optional[Request] = None) -> None:
    # Callers pass the login session ``issue_login_token`` just minted; the
    # cookie is that typed slot, so its holder is never re-guessed by spelling.
    if not token:
        return
    response.set_cookie(value=token, **_cookie_kwargs(request))


def clear_login_cookie(response: Response, request: Optional[Request] = None) -> None:
    kwargs = _cookie_kwargs(request)
    kwargs.pop("max_age", None)
    response.delete_cookie(
        key=kwargs.pop("key"),
        path=kwargs.get("path", "/"),
        domain=kwargs.get("domain"),
        secure=kwargs.get("secure", False),
        httponly=True,
        samesite=kwargs.get("samesite", "lax"),
    )


def cookie_login_token(request: Optional[Request]) -> str:
    """The session cookie's value, or empty.

    Only a login session is ever stored here, and auth resolves a cookie
    credential against the login-session table alone -- an unknown value
    simply finds no row.
    """
    if request is None:
        return ""
    return (request.cookies.get(LOGIN_COOKIE_NAME) or "").strip()
