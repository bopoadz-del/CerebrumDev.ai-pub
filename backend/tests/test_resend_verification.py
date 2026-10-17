"""Unverified login tokens can resend verification without a 403."""

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
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_S", raising=False)

    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    from app.routers import accounts, resend_verification

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(resend_verification.router, prefix="/v1/auth")
    return TestClient(app)


def _register(client, email="resend@example.com", password="pilot-pass-123"):
    res = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def test_resend_verification_works_while_unverified(client, monkeypatch):
    body = _register(client)
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
