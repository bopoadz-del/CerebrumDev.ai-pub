"""Item 13: admin-only server-side "mark this account email-verified" for
test accounts, so testers are not stuck at email verification.

Verification stays on (ACCOUNTS_REQUIRE_VERIFIED_EMAIL=1) in every test; the
only way past it here is the real token flow or the master-key admin action.
All accounts and keys are synthetic and live in pytest's tmp_path.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MASTER = "synthetic-master-key"
ADMIN = {"X-API-Key": MASTER}
PASSWORD = "synthetic-pass-123"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", MASTER)
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "1")
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.delenv("ACCOUNTS_EXPOSE_DEV_TOKENS", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()
    from app.routers import accounts

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    return TestClient(app)


def _register(client, email):
    reg = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert reg.status_code == 201, reg.text
    return reg.json()["account_id"]


def _me_status(client, email):
    """Log in, then hit a verification-gated route with the login token."""
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["login_token"]
    return client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def _verified(account_id):
    from app.core.accounts_store import get_account

    return get_account(account_id)["email_verified"]


def test_admin_marks_account_verified_and_it_can_then_sign_in(client):
    acct = _register(client, "tester-a@example.test")
    assert _me_status(client, "tester-a@example.test") == 403

    res = client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": acct})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "account_id": acct, "email_verified": True}
    assert _verified(acct) is True
    assert _me_status(client, "tester-a@example.test") == 200


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "not-the-master"}, "user"])
def test_non_admin_or_anonymous_is_refused_and_flag_unchanged(client, headers):
    acct = _register(client, "tester-b@example.test")
    if headers == "user":
        login = client.post(
            "/v1/auth/login", json={"email": "tester-b@example.test", "password": PASSWORD}
        )
        headers = {"Authorization": f"Bearer {login.json()['login_token']}"}

    res = client.post("/v1/auth/admin/verify-email", headers=headers, json={"account_id": acct})

    assert res.status_code in (401, 403), res.text
    assert _verified(acct) is False
    assert _me_status(client, "tester-b@example.test") == 403


def test_two_accounts_give_two_results(client):
    marked = _register(client, "marked@example.test")
    left = _register(client, "left@example.test")

    client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": marked})

    assert (_verified(marked), _verified(left)) == (True, False)
    assert (_me_status(client, "marked@example.test"), _me_status(client, "left@example.test")) == (200, 403)


def test_unknown_or_blank_account_is_refused_not_created(client):
    assert client.post(
        "/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": "acct_missing"}
    ).status_code == 404
    assert client.post(
        "/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": "  "}
    ).status_code == 400


def test_unverified_user_cannot_skip_verification(client):
    acct = _register(client, "plain@example.test")
    # No admin action: the gate still refuses, and the bad-token path still 400s.
    assert _me_status(client, "plain@example.test") == 403
    assert client.post("/v1/auth/verify-email", json={"token": "cdv_bogus"}).status_code == 400
    assert _verified(acct) is False


def test_a_verified_non_admin_user_cannot_verify_another_account(client):
    insider = _register(client, "insider@example.test")
    client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": insider})
    assert _me_status(client, "insider@example.test") == 200
    victim = _register(client, "other@example.test")
    login = client.post(
        "/v1/auth/login", json={"email": "insider@example.test", "password": PASSWORD}
    )
    user = {"Authorization": f"Bearer {login.json()['login_token']}"}

    res = client.post("/v1/auth/admin/verify-email", headers=user, json={"account_id": victim})

    assert res.status_code in (401, 403), res.text
    assert _verified(victim) is False
