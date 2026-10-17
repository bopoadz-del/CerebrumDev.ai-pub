"""CORS allowlist — local boots must not inherit production origins.

``CORS_ALLOW_ORIGINS`` (comma-separated) is always honoured when set: that is
how production pins ``https://www.cerebrum-dev.com`` and the legacy onrender
frontend. The default list is environment-dependent:

* ``ALLOW_ANONYMOUS_DEV`` or a non-production ``ENV`` → local frontend only.
  Production hosts are stripped even if ``FRONTEND_URL`` points at one, so a
  laptop ``.env``-less boot cannot make the live Floor a legal Origin.
* Production without an explicit list → the factory's own browser origins.

Do not add ``https://api.cerebrum-dev.com`` as a browser origin; that is the
API, not a page that calls it.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}
_PROD_ENVS = {"production", "prod"}

# Hosts that must never appear on a local / anonymous-dev default allowlist.
PRODUCTION_ORIGINS: frozenset[str] = frozenset(
    {
        "https://cerebrum-dev.com",
        "https://www.cerebrum-dev.com",
        "https://api.cerebrum-dev.com",
        "https://cerebrumdev-frontend-kkz2.onrender.com",
    }
)

# Production default when CORS_ALLOW_ORIGINS is unset. Matches render.yaml.
PRODUCTION_DEFAULT_ALLOWLIST: tuple[str, ...] = (
    "https://cerebrum-dev.com",
    "https://www.cerebrum-dev.com",
    "https://cerebrumdev-frontend-kkz2.onrender.com",
)

LOCAL_DEFAULT_ORIGIN = "http://localhost:5173"


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def _is_production(env: str | None = None) -> bool:
    value = (env if env is not None else os.getenv("ENV", "development")).strip().lower()
    return value in _PROD_ENVS


def _parse_origins(raw: str) -> list[str]:
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


def _normalize(origin: str) -> str:
    return origin.strip().rstrip("/")


def is_production_origin(origin: str) -> bool:
    return _normalize(origin) in PRODUCTION_ORIGINS


def cors_allow_origins(
    *,
    explicit: str | None = None,
    frontend_url: str | None = None,
    allow_anonymous_dev: bool | None = None,
    env: str | None = None,
) -> list[str]:
    """Return the CORS allowlist for this process.

    ``explicit`` / ``frontend_url`` / ``allow_anonymous_dev`` / ``env`` default
    to the live environment so ``main`` can call this with no arguments, and
    tests can pin each axis without mutating ``os.environ``.
    """
    raw_explicit = explicit
    if raw_explicit is None:
        raw_explicit = os.getenv("CORS_ALLOW_ORIGINS")
    if raw_explicit is not None and raw_explicit.strip():
        return _parse_origins(raw_explicit)

    if frontend_url is None:
        frontend_url = os.getenv("FRONTEND_URL", LOCAL_DEFAULT_ORIGIN)
    frontend = _normalize(frontend_url) if frontend_url else LOCAL_DEFAULT_ORIGIN

    anonymous = (
        _truthy("ALLOW_ANONYMOUS_DEV")
        if allow_anonymous_dev is None
        else allow_anonymous_dev
    )
    if anonymous or not _is_production(env):
        if frontend and not is_production_origin(frontend):
            return [frontend]
        return [LOCAL_DEFAULT_ORIGIN]

    origins = [_normalize(item) for item in PRODUCTION_DEFAULT_ALLOWLIST]
    if frontend and frontend not in origins:
        origins.insert(0, frontend)
    return origins
