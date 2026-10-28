"""PR #51 — build routes fail closed AT THE ROUTE for a no-tenant or
cross-tenant principal, and admin-issued tenant-scoped API tokens.

ORDERS_28OCT_0230Z #1(b) / 0245Z #5. The worker already refuses an unbound
build (no_authenticated_tenant); these lock the refusal at the route, before a
job or workspace is created, and prove the tenant-scoped token path a
programmatic caller must use instead of the master key.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import accounts_store
from app.core.auth import Principal, _resolve_user_principal
from app.routers.session_product import _require_build_tenant


class _State:
    """Minimal stand-in for a SessionState: only the owner field is read."""

    def __init__(self, owner: str) -> None:
        self.user_id = owner


# --- route guard (unit, no HTTP) --------------------------------------------

def test_no_tenant_principal_is_refused_403_before_any_job():
    st = _State("acct_owner")
    for p in (
        Principal(kind="admin"),
        Principal(kind="user", account_id=None),
        Principal(kind="user", account_id=""),
    ):
        with pytest.raises(HTTPException) as exc:
            _require_build_tenant(st, p)
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "no_tenant"


def test_open_dev_principal_is_allowed_non_production_only():
    # ALLOW_ANONYMOUS_DEV is a single implicit local/test tenant and can never
    # arise in production (prod requires a master key or explicit opt-in), so
    # the build guard lets it through rather than break the local build path.
    st = _State("acct_owner")
    assert _require_build_tenant(st, Principal(kind="dev")) == ""


def test_cross_tenant_user_is_refused_403():
    st = _State("acct_owner")
    with pytest.raises(HTTPException) as exc:
        _require_build_tenant(st, Principal(kind="user", account_id="acct_intruder"))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "tenant_mismatch"


def test_same_tenant_user_is_allowed_and_returns_the_account():
    st = _State("acct_owner")
    got = _require_build_tenant(st, Principal(kind="user", account_id="acct_owner"))
    assert got == "acct_owner"


# --- admin-issued tenant-scoped tokens (HTTP) -------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")
    monkeypatch.delenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()
    from app.routers import accounts

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    return TestClient(app)


def _make_account(client, email):
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "pilot-pass-123"}
    )
    assert reg.status_code == 201, reg.text
    return reg.json()["account_id"]


def test_admin_issues_tenant_scoped_token_resolves_revokes_and_is_single_tenant(
    client, monkeypatch
):
    acct_a = _make_account(client, "a@example.com")
    acct_b = _make_account(client, "b@example.com")

    # Turn on the master key: now admin-only routes are reachable with it.
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-test-key")
    master = {"X-API-Key": "master-test-key"}

    # A non-admin (open-dev, no master header) cannot mint tenant keys.
    denied = client.post("/v1/auth/admin/keys", json={"account_id": acct_a})
    assert denied.status_code in (401, 403)

    # Admin issues a tenant-scoped key for account A.
    res = client.post(
        "/v1/auth/admin/keys", headers=master, json={"account_id": acct_a}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["account_id"] == acct_a
    api_key = body["api_key"]
    key_id = body["key_id"]

    # It resolves to a USER principal bound to account A (and no other).
    p = _resolve_user_principal(api_key)
    assert p is not None and p.kind == "user" and p.account_id == acct_a

    # That token passes the build guard for A's session, and is refused
    # (tenant_mismatch) for B's session — single-tenant by construction.
    assert _require_build_tenant(_State(acct_a), p) == acct_a
    with pytest.raises(HTTPException) as exc:
        _require_build_tenant(_State(acct_b), p)
    assert exc.value.status_code == 403 and exc.value.detail["error"] == "tenant_mismatch"

    # Issuing for a non-existent account fails closed.
    missing = client.post(
        "/v1/auth/admin/keys", headers=master, json={"account_id": "acct_nope"}
    )
    assert missing.status_code == 404

    # Revoke (admin) -> the token no longer resolves.
    rev = client.request(
        "DELETE", f"/v1/auth/admin/keys/{acct_a}/{key_id}", headers=master
    )
    assert rev.status_code == 200, rev.text
    assert _resolve_user_principal(api_key) is None
