"""HttpOnly ``cdt`` cookie login for the Floor SPA.

API clients still use ``Authorization: Bearer cdt_…`` / master key / ``cdk_``.
The cookie is an additional browser path: Secure + HttpOnly + SameSite=None
in production (Lax on non-secure TestClient).
"""

from __future__ import annotations

import secrets
import uuid

from fastapi.testclient import TestClient

from app.core.auth_cookies import LOGIN_COOKIE_NAME
from app.main import app


def _cookie_client(monkeypatch, tmp_path, *, env: str = "test"):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret-key-value")
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "0")
    monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_DOMAIN", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SAMESITE", raising=False)
    return TestClient(app)


def _register(client: TestClient, email: str, password: str = "pilot-pass-123") -> dict:
    res = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def _set_cookie_header(response) -> str:
    # Starlette may emit set-cookie more than once; join for flag checks.
    raw = response.headers.get("set-cookie") or ""
    if not raw:
        raw = ", ".join(response.headers.getlist("set-cookie"))
    return raw


def test_login_sets_httponly_samesite_lax_cookie(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"cookie-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)

    res = client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    assert res.status_code == 200, res.text
    assert res.json()["login_token"].startswith("cdt_")

    header = _set_cookie_header(res).lower()
    assert LOGIN_COOKIE_NAME in header
    assert "httponly" in header
    assert "samesite=lax" in header  # ENV=test → non-secure → Lax
    token = res.cookies.get(LOGIN_COOKIE_NAME)
    assert token and token.startswith("cdt_")
    # Browsers exclude HttpOnly cookies from document.cookie. This flag is
    # the server-side guarantee that JS cannot read the session token.
    assert "httponly" in header


def test_js_cannot_read_httponly_cookie_name_from_document_cookie_contract(
    monkeypatch, tmp_path
):
    """Pin the HttpOnly contract the browser enforces for document.cookie.

    A non-HttpOnly cookie would appear in ``document.cookie`` as
    ``cdt=<token>``. HttpOnly forbids that. We assert the Set-Cookie flag
    rather than spinning a browser here; Playwright e2e is unwired in CI.
    """
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"js-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    res = client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    header = _set_cookie_header(res)
    assert "HttpOnly" in header or "httponly" in header.lower()
    # The raw token must not be offered as a non-HttpOnly sibling cookie.
    for part in header.split(","):
        if LOGIN_COOKIE_NAME + "=" in part or f"{LOGIN_COOKIE_NAME}=" in part.lower():
            assert "httponly" in part.lower()


def test_cookie_authenticates_without_authorization_header(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"me-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    login = client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    assert login.status_code == 200
    # TestClient stores the Set-Cookie; no Bearer header.
    me = client.get("/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


def test_unauth_after_cookie_clear(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"clear-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    assert client.get("/v1/auth/me").status_code == 200

    client.cookies.clear()
    res = client.get("/v1/auth/me")
    assert res.status_code == 401, res.text
    assert res.json()["detail"] == "Invalid or missing API key"


def test_logout_clears_cookie_then_unauth(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"out-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    assert client.get("/v1/auth/me").status_code == 200

    logged_out = client.post("/v1/auth/logout")
    assert logged_out.status_code == 200
    header = _set_cookie_header(logged_out).lower()
    assert LOGIN_COOKIE_NAME in header
    assert "max-age=0" in header or "max-age=0" in header.replace(" ", "")

    client.cookies.clear()
    assert client.get("/v1/auth/me").status_code == 401


def test_bearer_header_still_works_without_cookie(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"hdr-{uuid.uuid4().hex[:8]}@example.com"
    body = _register(client, email)
    fresh = TestClient(app)
    res = fresh.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {body['login_token']}"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["email"] == email


def test_production_cookie_is_secure_samesite_none(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path, env="production")
    email = f"sec-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    res = client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    header = _set_cookie_header(res).lower()
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=none" in header


def test_auth_cookie_samesite_env_override(monkeypatch, tmp_path):
    client = _cookie_client(monkeypatch, tmp_path, env="production")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")
    email = f"ovr-{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    res = client.post("/v1/auth/login", json={"email": email, "password": "pilot-pass-123"})
    header = _set_cookie_header(res).lower()
    assert "samesite=lax" in header
    assert "secure" in header


def test_cookie_is_not_a_master_key_channel(monkeypatch, tmp_path):
    """Master key stays header-only. A cookie named cdt with the master key is ignored."""
    client = _cookie_client(monkeypatch, tmp_path)
    client.cookies.set(LOGIN_COOKIE_NAME, "master-secret-key-value")
    res = client.get("/v1/auth/me")
    assert res.status_code == 401


def test_cookie_is_not_an_api_key_channel(monkeypatch, tmp_path):
    """The cookie only ever holds a login session, so it resolves against the
    login-session table alone -- a live API key placed in it authenticates
    nothing. Decided by where the credential came from, not by its spelling."""
    client = _cookie_client(monkeypatch, tmp_path)
    email = f"key-{uuid.uuid4().hex[:8]}@example.com"
    login = _register(client, email)["login_token"]
    key = client.post(
        "/v1/auth/keys",
        json={"label": "ci"},
        headers={"Authorization": f"Bearer {login}"},
    )
    assert key.status_code == 201, key.text
    api_key = key.json()["api_key"]
    client.cookies.clear()
    assert client.get("/v1/auth/me", headers={"X-API-Key": api_key}).status_code == 200

    client.cookies.set(LOGIN_COOKIE_NAME, api_key)
    assert client.get("/v1/auth/me").status_code == 401


def test_a_login_session_is_known_by_lookup_not_by_its_prefix(monkeypatch, tmp_path):
    """The store knows what it minted: a value wearing the session label
    that the store never minted is not a session."""
    client = _cookie_client(monkeypatch, tmp_path)
    client.cookies.clear()
    res = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer cdt_never-minted-{secrets.token_hex(12)}"}
    )
    assert res.status_code == 401
