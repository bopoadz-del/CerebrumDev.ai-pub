"""CI-collected session ownership tests.

``backend/app/tests/test_session_ownership.py`` is not on pytest's
``testpaths``. These lock the same rule on the factory path that production
uses: tell-the-factory / receive-platform.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core import accounts_store, session_store
from app.core.auth import Principal
from app.core.session_guard import owned_session_or_404, require_owned_session
from app.main import app
from app.models.session import SessionState
from fastapi.testclient import TestClient


def test_owner_user_passes():
    state = SessionState(session_id="sess_guard_ci", user_id="acct_owner")
    session_store._session_store[state.session_id] = state
    try:
        got = owned_session_or_404(state.session_id, Principal(kind="user", account_id="acct_owner"))
        assert got.session_id == state.session_id
    finally:
        session_store._session_store.pop(state.session_id, None)


def test_cross_account_user_gets_non_leaking_404():
    state = SessionState(session_id="sess_guard_ci2", user_id="acct_owner")
    session_store._session_store[state.session_id] = state
    try:
        with pytest.raises(HTTPException) as exc:
            owned_session_or_404(
                state.session_id, Principal(kind="user", account_id="acct_intruder")
            )
        assert exc.value.status_code == 404
        assert exc.value.detail == "Session not found"
    finally:
        session_store._session_store.pop(state.session_id, None)


def test_missing_session_same_404_as_cross_account():
    with pytest.raises(HTTPException) as exc:
        owned_session_or_404(
            "sess_does_not_exist", Principal(kind="user", account_id="acct_owner")
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Session not found"


def test_admin_and_dev_principals_pass():
    state = SessionState(session_id="sess_guard_ci3", user_id="acct_owner")
    session_store._session_store[state.session_id] = state
    try:
        assert owned_session_or_404(state.session_id, Principal(kind="admin")).session_id == state.session_id
        assert owned_session_or_404(state.session_id, Principal(kind="dev")).session_id == state.session_id
    finally:
        session_store._session_store.pop(state.session_id, None)


def test_require_owned_session_dependency_matches_sync_helper():
    import asyncio

    state = SessionState(session_id="sess_guard_ci4", user_id="acct_owner")
    session_store._session_store[state.session_id] = state
    try:
        principal = Principal(kind="user", account_id="acct_intruder")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_owned_session(state.session_id, principal))
        assert exc.value.status_code == 404
    finally:
        session_store._session_store.pop(state.session_id, None)


@pytest.fixture()
def two_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "0")
    monkeypatch.setenv("ARCHITECT_LLM_DRAFTING_ENABLED", "0")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    a = accounts_store.create_account(f"a-{uuid.uuid4().hex[:8]}@example.com", "hunter2hunter2")
    b = accounts_store.create_account(f"b-{uuid.uuid4().hex[:8]}@example.com", "hunter2hunter2")
    tok_a = accounts_store.issue_login_token(a["account_id"])
    tok_b = accounts_store.issue_login_token(b["account_id"])
    client = TestClient(app)
    return client, a, tok_a, b, tok_b


def test_cross_account_cannot_read_or_draft_product(two_accounts):
    """The tell-the-factory path must not leak another account's design."""
    client, a, tok_a, _b, tok_b = two_accounts
    created = client.post("/v1/sessions/", headers={"Authorization": f"Bearer {tok_a}"})
    assert created.status_code == 200, created.text
    sid = created.json()["session_id"]
    assert created.json()["user_id"] == a["account_id"]

    ok = client.get(
        f"/v1/sessions/{sid}/product",
        headers={"Authorization": f"Bearer {tok_a}"},
    )
    assert ok.status_code == 200, ok.text

    denied = client.get(
        f"/v1/sessions/{sid}/product",
        headers={"Authorization": f"Bearer {tok_b}"},
    )
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Session not found"

    draft = client.post(
        f"/v1/sessions/{sid}/product/draft",
        headers={"Authorization": f"Bearer {tok_b}"},
        json={"brief": "steal this platform"},
    )
    assert draft.status_code == 404
    assert draft.json()["detail"] == "Session not found"

    chat = client.post(
        f"/v1/sessions/{sid}/chat",
        headers={"Authorization": f"Bearer {tok_b}"},
        json={"message": "hello"},
    )
    assert chat.status_code == 404
