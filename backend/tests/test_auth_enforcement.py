"""Master-key compare must 401 on a wrong-length token, not 500.

Production runs Python 3.11. ``hmac.compare_digest`` on raw strings of
different lengths raises there, which would crash every signed-in request:
login tokens (``cdt_…``) are not the same length as ``CEREBRUM_DEV_API_KEY``.
"""

from __future__ import annotations

import uuid

from app.core import accounts_store
from app.core.auth import _tokens_match
from app.main import app
from fastapi.testclient import TestClient


def test_tokens_match_accepts_equal_and_rejects_different_lengths():
    assert _tokens_match("same-length-key!!", "same-length-key!!") is True
    assert _tokens_match("short", "a-much-longer-master-key") is False
    assert _tokens_match("", "master") is False
    assert _tokens_match("cdt_abc", "master-secret-key-value") is False


def test_wrong_length_bearer_is_401_not_500(monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret-key-value")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    client = TestClient(app)
    res = client.get("/v1/auth/me", headers={"Authorization": "Bearer short"})
    assert res.status_code == 401, res.text
    assert res.json()["detail"] == "Invalid or missing API key"


def test_login_token_works_when_master_key_is_a_different_length(monkeypatch, tmp_path):
    """The live path: master key is set, caller presents a cdt_ login token."""
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "mk")
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "0")
    account = accounts_store.create_account(
        f"len-{uuid.uuid4().hex[:8]}@example.com", "hunter2hunter2"
    )
    token = accounts_store.issue_login_token(account["account_id"])
    assert len(token) != len("mk")
    client = TestClient(app)
    res = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert res.json()["account_id"] == account["account_id"]
