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
    from app.routers.accounts import router

    app = FastAPI()
    app.include_router(router, prefix="/v1/auth")
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


@pytest.mark.parametrize("path, endpoint", [
    ("/admin/verify-email", "admin_mark_verified"),
    ("/admin/keys", "admin_issue_tenant_key"),
])
def test_admin_routes_are_served_by_their_handlers(path, endpoint):
    import app.routers.accounts as accounts_router

    (route,) = [r for r in accounts_router.router.routes if r.path == path]
    assert route.endpoint is getattr(accounts_router, endpoint)
    assert route.methods == {"POST"}


@pytest.mark.parametrize("email", ["tester-a@example.test", "tester-z@example.test"])
def test_admin_marks_account_verified_and_it_can_then_sign_in(client, email):
    acct = _register(client, email)
    assert _me_status(client, email) == 403

    res = client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": acct})

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "account_id": acct, "email_verified": True}
    assert _verified(acct) is True
    assert _me_status(client, email) == 200


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


@pytest.mark.parametrize("marked_email, left_email", [
    ("marked@example.test", "left@example.test"),
    ("left@example.test", "marked@example.test"),
])
def test_two_accounts_give_two_results(client, marked_email, left_email):
    marked = _register(client, marked_email)
    left = _register(client, left_email)

    client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": marked})

    assert (_verified(marked), _verified(left)) == (True, False)
    assert (_me_status(client, marked_email), _me_status(client, left_email)) == (200, 403)


@pytest.mark.parametrize("account_id, status", [("acct_missing", 404), ("  ", 400)])
def test_unknown_or_blank_account_is_refused_not_created(client, account_id, status):
    from app.core.accounts_store import get_account

    res = client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": account_id})

    assert res.status_code == status, res.text
    assert get_account(account_id.strip()) is None


@pytest.mark.parametrize("bogus", ["cdv_bogus", "not-a-token"])
def test_unverified_user_cannot_skip_verification(client, bogus):
    acct = _register(client, "plain@example.test")
    # No admin action: the gate still refuses, and the bad-token path still 400s.
    assert _me_status(client, "plain@example.test") == 403
    assert client.post("/v1/auth/verify-email", json={"token": bogus}).status_code == 400
    assert _verified(acct) is False


@pytest.mark.parametrize("victim_email", ["other@example.test", "another@example.test"])
def test_a_verified_non_admin_user_cannot_verify_another_account(client, victim_email):
    insider = _register(client, "insider@example.test")
    client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": insider})
    assert _me_status(client, "insider@example.test") == 200
    victim = _register(client, victim_email)
    login = client.post(
        "/v1/auth/login", json={"email": "insider@example.test", "password": PASSWORD}
    )
    user = {"Authorization": f"Bearer {login.json()['login_token']}"}

    res = client.post("/v1/auth/admin/verify-email", headers=user, json={"account_id": victim})

    assert res.status_code in (401, 403), res.text
    assert _verified(victim) is False


def _verify_row(account_id):
    from app.core import accounts_store as store

    cols = store._t_accounts.c
    with store._engine().begin() as conn:
        row = conn.execute(
            store.sa.select(cols.verify_token_hash, cols.verify_expires_at)
            .where(cols.id == account_id)
        ).one()
    return row[0], row[1]


@pytest.mark.parametrize("email", ["replay-a@example.test", "replay-z@example.test"])
def test_admin_verify_clears_token_and_old_token_is_refused(client, email):
    import app.routers.accounts as accounts_router

    acct = _register(client, email)
    old_token = accounts_router.accounts_store.issue_verify_token(acct)
    assert _verify_row(acct) != (None, None)

    res = client.post("/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": acct})

    assert res.status_code == 200, res.text
    assert _verify_row(acct) == (None, None)
    replay = client.post("/v1/auth/verify-email", json={"token": old_token})
    assert replay.status_code == 400, replay.status_code


@pytest.mark.parametrize("pad", ["  {}  ", "\t{}\n"])
def test_padded_account_id_is_stripped_in_response(client, pad):
    import app.routers.accounts as accounts_router

    acct = _register(client, "padded@example.test")
    assert accounts_router.admin_mark_verified is not None

    res = client.post(
        "/v1/auth/admin/verify-email", headers=ADMIN, json={"account_id": pad.format(acct)}
    )

    assert res.status_code == 200, res.text
    assert res.json()["account_id"] == acct
    assert _verified(acct) is True
