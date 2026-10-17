"""P0 trust layer: accounts, per-user API keys, session ownership."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    storage_path = str(tmp_path / "storage")
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    import app.core.session_persistence as session_persistence

    monkeypatch.setattr(session_persistence, "STORAGE_PATH", storage_path)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")
    monkeypatch.delenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", raising=False)
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
    monkeypatch.delenv("SMOKE_ACCOUNT_PASSWORD", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_S", raising=False)

    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    from app.routers import accounts, resend_verification, sessions

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(resend_verification.router, prefix="/v1/auth")
    app.include_router(sessions.router, prefix="/v1/sessions")
    return TestClient(app)


def _register(client, email="dev@example.com", password="pilot-pass-123"):
    res = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def test_register_login_verify_and_me(client):
    body = _register(client)
    assert body["account_id"].startswith("acct_")
    assert body["email_verified"] is False
    token = body["verification"]["dev_verification_token"]
    assert token.startswith("cdv_")

    res = client.post("/v1/auth/verify-email", json={"token": token})
    assert res.status_code == 200
    assert res.json()["email_verified"] is True

    res = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {body['login_token']}"}
    )
    assert res.status_code == 200
    me = res.json()
    assert me["email"] == "dev@example.com"
    assert me["email_verified"] is True

    res = client.post(
        "/v1/auth/login", json={"email": "dev@example.com", "password": "wrong-pass"}
    )
    assert res.status_code == 401
    res = client.post(
        "/v1/auth/login", json={"email": "dev@example.com", "password": "pilot-pass-123"}
    )
    assert res.status_code == 200
    assert res.json()["login_token"].startswith("cdt_")


def test_duplicate_email_rejected(client):
    _register(client)
    res = client.post(
        "/v1/auth/register",
        json={"email": "dev@example.com", "password": "pilot-pass-123"},
    )
    assert res.status_code == 409


def test_short_password_rejected(client):
    res = client.post(
        "/v1/auth/register", json={"email": "a@b.co", "password": "short"}
    )
    assert res.status_code == 422


def test_api_keys_and_session_ownership(client):
    a = _register(client, "a@example.com")
    b = _register(client, "b@example.com")

    key = client.post(
        "/v1/auth/keys",
        json={"label": "ci"},
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert key.status_code == 201, key.text
    api_key = key.json()["api_key"]
    assert api_key.startswith("cdk_")

    created = client.post("/v1/sessions/", headers={"X-API-Key": api_key})
    assert created.status_code == 200, created.text
    sess = created.json()
    assert sess["user_id"] == a["account_id"]

    ok = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert ok.status_code == 200
    denied = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": f"Bearer {b['login_token']}"},
    )
    assert denied.status_code == 404

    mine = client.get(
        "/v1/sessions/", headers={"Authorization": f"Bearer {a['login_token']}"}
    )
    assert any(s["session_id"] == sess["session_id"] for s in mine.json())
    other = client.get(
        "/v1/sessions/", headers={"Authorization": f"Bearer {b['login_token']}"}
    )
    assert other.json() == []

    key_id = key.json()["key_id"]
    res = client.delete(
        f"/v1/auth/keys/{key_id}",
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert res.status_code == 200
    res = client.post("/v1/sessions/", headers={"X-API-Key": api_key})
    assert res.json()["user_id"] == "dev-local"


def test_master_key_admin_access(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    a = _register(client, "owner@example.com")
    created = client.post(
        "/v1/sessions/", headers={"Authorization": f"Bearer {a['login_token']}"}
    )
    sess = created.json()

    res = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": "Bearer master-secret"},
    )
    assert res.status_code == 200
    res = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert res.status_code == 200
    res = client.get(
        f"/v1/sessions/{sess['session_id']}",
        headers={"Authorization": "Bearer garbage"},
    )
    assert res.status_code == 401


def test_me_requires_account_credential(client):
    res = client.get("/v1/auth/me")
    # 401 so the Floor SPA opens Sign in (403 was painted as unreachable).
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid or missing API key"


def test_password_reset_flow(client):
    body = _register(client)
    old_token = body["login_token"]

    res = client.post("/v1/auth/forgot-password", json={"email": "dev@example.com"})
    assert res.status_code == 200
    token = res.json()["dev_reset_token"]
    assert token.startswith("cdr_")

    res = client.post(
        "/v1/auth/reset-password",
        json={"token": token, "new_password": "new-pass-456"},
    )
    assert res.status_code == 200

    assert (
        client.post(
            "/v1/auth/login",
            json={"email": "dev@example.com", "password": "pilot-pass-123"},
        ).status_code
        == 401
    )
    ok = client.post(
        "/v1/auth/login", json={"email": "dev@example.com", "password": "new-pass-456"}
    )
    assert ok.status_code == 200

    res = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    # Revoked login token is not a user principal; 401 opens Sign in.
    assert res.status_code == 401

    res = client.post(
        "/v1/auth/reset-password",
        json={"token": token, "new_password": "another-pass-789"},
    )
    assert res.status_code == 400


def test_forgot_password_unknown_email_no_token(client):
    res = client.post("/v1/auth/forgot-password", json={"email": "ghost@example.com"})
    assert res.status_code == 200
    assert "dev_reset_token" not in res.json()


def test_change_password_happy_path_keeps_current_session(client):
    body = _register(client, "change@example.com", "old-pass-123")
    current = body["login_token"]
    other = client.post(
        "/v1/auth/login",
        json={"email": "change@example.com", "password": "old-pass-123"},
    ).json()["login_token"]
    assert other != current

    res = client.post(
        "/v1/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "new-pass-456"},
        headers={"Authorization": f"Bearer {current}"},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["ok"] is True
    assert payload["account_id"] == body["account_id"]
    assert "Other sessions" in payload["message"]

    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {current}"}).status_code
        == 200
    )
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": "change@example.com", "password": "old-pass-123"},
        ).status_code
        == 401
    )
    ok = client.post(
        "/v1/auth/login",
        json={"email": "change@example.com", "password": "new-pass-456"},
    )
    assert ok.status_code == 200


def test_change_password_wrong_current_is_403(client):
    body = _register(client, "wrongpw@example.com", "old-pass-123")
    token = body["login_token"]
    res = client.post(
        "/v1/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "new-pass-456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "Current password is incorrect"
    still = client.post(
        "/v1/auth/login",
        json={"email": "wrongpw@example.com", "password": "old-pass-123"},
    )
    assert still.status_code == 200
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_change_password_requires_account_and_length(client):
    assert client.post(
        "/v1/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "new-pass-456"},
    ).status_code == 401
    body = _register(client, "shortpw@example.com", "old-pass-123")
    res = client.post(
        "/v1/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "short"},
        headers={"Authorization": f"Bearer {body['login_token']}"},
    )
    assert res.status_code == 422


def test_verified_email_enforcement(client, monkeypatch):
    body = _register(client)
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "1")

    res = client.post(
        "/v1/sessions/", headers={"Authorization": f"Bearer {body['login_token']}"}
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "email_not_verified"

    token = body["verification"]["dev_verification_token"]
    assert client.post("/v1/auth/verify-email", json={"token": token}).status_code == 200

    res = client.post(
        "/v1/sessions/", headers={"Authorization": f"Bearer {body['login_token']}"}
    )
    assert res.status_code == 200


def test_resend_verification_works_while_unverified(client, monkeypatch):
    """Unverified login tokens must still be able to request a new mail."""
    body = _register(client, "resend@example.com")
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "1")
    headers = {"Authorization": f"Bearer {body['login_token']}"}

    denied = client.get("/v1/auth/me", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "email_not_verified"

    res = client.post("/v1/auth/resend-verification", headers=headers)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["ok"] is True
    assert payload["already_verified"] is False
    token = payload["verification"]["dev_verification_token"]
    assert token.startswith("cdv_")
    assert token != body["verification"]["dev_verification_token"]

    assert client.post("/v1/auth/verify-email", json={"token": token}).status_code == 200
    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email_verified"] is True

    again = client.post("/v1/auth/resend-verification", headers=headers)
    assert again.status_code == 200
    assert again.json()["already_verified"] is True


def test_resend_verification_requires_account_credential(client):
    res = client.post("/v1/auth/resend-verification")
    assert res.status_code == 401


def test_auth_rate_limit_register(client, monkeypatch):
    monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "3")
    for i in range(3):
        res = client.post(
            "/v1/auth/register",
            json={"email": f"user{i}@example.com", "password": "pilot-pass-123"},
        )
        assert res.status_code == 201, res.text
    res = client.post(
        "/v1/auth/register",
        json={"email": "user3@example.com", "password": "pilot-pass-123"},
    )
    assert res.status_code == 429
    assert res.json()["detail"] == "rate_limited"


def test_database_url_defaults_to_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    from app.core import accounts_store

    url = accounts_store._database_url()
    assert url.startswith("sqlite:///")
    assert url.endswith("accounts.db")


def test_database_url_normalizes_postgres(monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", "postgres://u:p@host:5432/db")
    from app.core import accounts_store

    assert accounts_store._database_url() == "postgresql+psycopg://u:p@host:5432/db"

    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", "postgresql://u:p@host:5432/db")
    assert accounts_store._database_url() == "postgresql+psycopg://u:p@host:5432/db"


def test_create_session_honors_requested_domain(client):
    a = _register(client)
    resp = client.post(
        "/v1/sessions/",
        json={"domain": "retail"},
        headers={"Authorization": f"Bearer {a['login_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["domain"] == "retail"


def test_unconfigured_mail_is_reported_honestly(client, monkeypatch):
    monkeypatch.delenv("ACCOUNTS_EXPOSE_DEV_TOKENS", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    res = client.post(
        "/v1/auth/register",
        json={"email": "unconf@example.com", "password": "pilot-pass-123"},
    )
    assert res.status_code == 201, res.text
    verification = res.json()["verification"]
    assert verification["mode"] == "unconfigured"
    assert verification["email_sent"] is False
    assert "Request a new one" not in verification["note"]
    assert "not yet enabled" in verification["note"]


def test_smoke_login_absent_without_gate(client):
    res = client.post("/v1/auth/smoke-login")
    assert res.status_code == 404


def test_smoke_login_rejects_wrong_gate(client, monkeypatch):
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "correct-gate-token")
    res = client.post("/v1/auth/smoke-login", headers={"X-Smoke-Gate": "wrong"})
    assert res.status_code == 401


def test_smoke_login_verified_principal_bypasses_public_unverified(client, monkeypatch):
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "correct-gate-token")
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "1")

    public = _register(client, "public@example.com")
    denied = client.post(
        "/v1/sessions/",
        headers={"Authorization": f"Bearer {public['login_token']}"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "email_not_verified"

    smoke = client.post(
        "/v1/auth/smoke-login", headers={"X-Smoke-Gate": "correct-gate-token"}
    )
    assert smoke.status_code == 200, smoke.text
    body = smoke.json()
    assert body["email_verified"] is True
    assert body["login_token"].startswith("cdt_")
    assert body["login_token_b"].startswith("cdt_")
    assert body["login_token"] != body["login_token_b"]

    created = client.post(
        "/v1/sessions/",
        headers={"Authorization": f"Bearer {body['login_token']}"},
    )
    assert created.status_code == 200, created.text
    sid = created.json()["session_id"]

    other = client.get(
        f"/v1/sessions/{sid}",
        headers={"Authorization": f"Bearer {body['login_token_b']}"},
    )
    assert other.status_code == 404
