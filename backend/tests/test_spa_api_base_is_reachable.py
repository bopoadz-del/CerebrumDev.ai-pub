"""The API base baked into the SPA must be a host that actually serves the API.

The public listener on :443 routes by HOST, not path:

    api.cerebrum-dev.com                        -> cerebrumdev-backend
    cerebrum-dev.com, www.cerebrum-dev.com      -> cerebrumdev-frontend
    projecteleven, www.projecteleven              -> the-fork

The :80 listener routes by PATH instead, sending /v1/*, /health, /ready,
/version, /docs and /openapi.json to the backend. Those two facts are why this
file exists: a same-origin base (``VITE_API_URL`` empty, which makes
frontend/src/api/factory.ts fall back to ``''``) is correct on :80 and dead on
:443. Over HTTPS, ``https://www.cerebrum-dev.com/v1/auth/me`` matches the
frontend host rule and returns this SPA's own index.html with status 200 and
content-type text/html. Every API call becomes a page of HTML parsed as JSON,
and :443 is what the public gets.

The other end of the range is just as broken: baking the load balancer's own
generated ``*.elb.amazonaws.com`` name as http:// means a page served over
HTTPS makes http requests, which browsers block as mixed content.

So the baked value must be the dedicated API host over https. Vite resolves it
at BUILD time (frontend/Dockerfile.prod takes it as a build arg), so a wrong
value is not a restart away from correct -- it ships inside the bundle and the
cutover looks done while the app is dead. These tests fail before that ships.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-aws-frontend.yml"

#: Hosts the :443 listener sends to the frontend target group. The SPA is
#: served from these, so none of them can also be its API base.
FRONTEND_HOSTS = frozenset({"cerebrum-dev.com", "www.cerebrum-dev.com"})

#: The host the :443 listener forwards to cerebrumdev-backend.
API_HOST = "api.cerebrum-dev.com"


def _baked_api_url() -> str:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    env = data.get("env") or {}
    assert "VITE_API_URL" in env, (
        "deploy-aws-frontend.yml must state VITE_API_URL explicitly; an absent "
        "key builds the bundle with an empty base, which is same-origin"
    )
    return str(env["VITE_API_URL"]).strip()


def test_api_base_is_not_same_origin() -> None:
    """Empty means same-origin, which :443 routes back to this same SPA."""
    url = _baked_api_url()
    assert url, (
        "VITE_API_URL is empty, so the SPA calls its own origin. On :443 the "
        "load balancer routes by host, so https://www.cerebrum-dev.com/v1/... "
        "returns this SPA's index.html (200, text/html) instead of the API."
    )


def test_api_base_is_https() -> None:
    """A page served over HTTPS cannot call http:// -- browsers block it."""
    scheme = urlparse(_baked_api_url()).scheme
    assert scheme == "https", (
        f"VITE_API_URL scheme is {scheme!r}; the page is served over HTTPS, so "
        "any http:// API base is mixed content and is blocked outright"
    )


def test_api_base_is_the_api_host_not_the_spa_host() -> None:
    host = urlparse(_baked_api_url()).hostname or ""
    assert host not in FRONTEND_HOSTS, (
        f"VITE_API_URL points at {host!r}, which the :443 listener forwards to "
        "cerebrumdev-frontend -- the SPA would be asking itself for the API"
    )
    assert host == API_HOST, (
        f"VITE_API_URL points at {host!r}; only {API_HOST!r} is routed to the "
        "backend target group"
    )


def test_api_base_is_not_the_load_balancers_own_name() -> None:
    """The generated ELB name is not a stable public hostname."""
    host = urlparse(_baked_api_url()).hostname or ""
    assert not host.endswith(".elb.amazonaws.com"), (
        f"VITE_API_URL bakes the load balancer's generated name ({host!r}) into "
        "the bundle; replacing the load balancer would then require a rebuild"
    )


def test_the_spa_origins_are_allowed_to_make_that_cross_origin_call() -> None:
    """Naming the API host makes the call cross-origin, so CORS must permit it.

    This is the half that is easy to forget: moving off same-origin is only
    safe because the hosts the SPA is served from are on the API's allowlist.
    """
    from app.core.cors_policy import PRODUCTION_DEFAULT_ALLOWLIST

    allowed = {origin.rstrip("/") for origin in PRODUCTION_DEFAULT_ALLOWLIST}
    for host in sorted(FRONTEND_HOSTS):
        origin = f"https://{host}"
        assert origin in allowed, (
            f"the SPA is served from {origin} and now calls the API cross-origin, "
            f"but {origin} is not in cors_policy.PRODUCTION_DEFAULT_ALLOWLIST"
        )
