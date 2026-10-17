"""CORS allowlist: local boots must not inherit production origins."""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware

from app.core.cors_policy import (
    LOCAL_DEFAULT_ORIGIN,
    PRODUCTION_DEFAULT_ALLOWLIST,
    PRODUCTION_ORIGINS,
    cors_allow_origins,
)
from app.main import app, _cors_origins


PROD_WWW = "https://www.cerebrum-dev.com"
PROD_API = "https://api.cerebrum-dev.com"
PROD_APEX = "https://cerebrum-dev.com"


class TestCorsAllowOriginsFunction:
    def test_anonymous_dev_default_excludes_production_hosts(self):
        origins = cors_allow_origins(
            explicit="",
            frontend_url="http://localhost:5173",
            allow_anonymous_dev=True,
            env="dev",
        )
        assert origins == ["http://localhost:5173"]
        assert not PRODUCTION_ORIGINS.intersection(origins)

    def test_non_production_env_without_anonymous_still_local(self):
        origins = cors_allow_origins(
            explicit="",
            frontend_url="http://localhost:5173",
            allow_anonymous_dev=False,
            env="development",
        )
        assert PROD_WWW not in origins
        assert PROD_API not in origins
        assert origins == ["http://localhost:5173"]

    def test_anonymous_dev_strips_frontend_url_when_it_is_production(self):
        origins = cors_allow_origins(
            explicit="",
            frontend_url=PROD_WWW,
            allow_anonymous_dev=True,
            env="dev",
        )
        assert origins == [LOCAL_DEFAULT_ORIGIN]
        assert PROD_WWW not in origins

    def test_production_default_keeps_factory_allowlist(self):
        origins = cors_allow_origins(
            explicit="",
            frontend_url=PROD_APEX,
            allow_anonymous_dev=False,
            env="production",
        )
        assert PROD_WWW in origins
        assert PROD_APEX in origins
        assert "https://cerebrumdev-frontend-kkz2.onrender.com" in origins
        assert set(PRODUCTION_DEFAULT_ALLOWLIST).issubset(origins)

    def test_explicit_list_is_honoured_even_in_anonymous_dev(self):
        raw = f"{PROD_APEX},{PROD_WWW}"
        origins = cors_allow_origins(
            explicit=raw,
            allow_anonymous_dev=True,
            env="dev",
        )
        assert origins == [PROD_APEX, PROD_WWW]


class TestImportedAppCors:
    def test_dev_boot_middleware_excludes_production_origins(self):
        """conftest sets ALLOW_ANONYMOUS_DEV=1 before app import."""
        assert PROD_WWW not in _cors_origins
        assert PROD_API not in _cors_origins
        assert not PRODUCTION_ORIGINS.intersection(_cors_origins)

        stacked = [
            mw.kwargs.get("allow_origins")
            for mw in app.user_middleware
            if mw.cls is CORSMiddleware
        ]
        assert stacked
        assert PROD_WWW not in stacked[0]
        assert PROD_API not in stacked[0]

    def test_localhost_origin_is_allowed_on_the_imported_app(self, client):
        res = client.get("/health", headers={"Origin": LOCAL_DEFAULT_ORIGIN})
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == LOCAL_DEFAULT_ORIGIN

    def test_production_origin_is_not_reflected_on_the_imported_app(self, client):
        res = client.get("/health", headers={"Origin": PROD_WWW})
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") != PROD_WWW
        assert res.headers.get("access-control-allow-origin") in {None, "null", ""}
