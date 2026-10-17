"""H2: Floor generate + approve-and-generate chat must honor require_entitled."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import accounts_store
from app.core.session_store import create_session, get_session, update_session
from app.main import app


@pytest.fixture()
def entitled_then_expired(tmp_path, monkeypatch):
    """Register while the trial is live, create a session, then expire the trial."""
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "0")
    monkeypatch.setenv("ARCHITECT_LLM_DRAFTING_ENABLED", "0")
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")

    account = accounts_store.create_account(
        f"floor-{uuid.uuid4().hex[:8]}@example.com", "hunter2hunter2"
    )
    account_id = account["account_id"]
    token = accounts_store.issue_login_token(account_id)
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    create_session(session_id, account_id)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, account_id, session_id


def _seed_approved_blueprint(session_id: str) -> None:
    state = get_session(session_id)
    assert state is not None
    state.product_design.brief = "a small tasting-room platform"
    state.product_design.blueprint = {
        "schema": "product_blueprint.v1",
        "product_id": "tasting-room",
        "product_name": "Tasting Room",
        "vertical": "winery",
        "summary": "Tanks and club",
        "capabilities": [
            {
                "id": "audit",
                "description": "Audit",
                "block_ids": ["audit"],
                "strategy_hint": "REUSE",
            }
        ],
    }
    state.product_design.blueprint_approved = True
    update_session(session_id, state)


def _expire_and_enforce(account_id: str, monkeypatch) -> None:
    accounts_store.set_subscription(account_id, "canceled")
    monkeypatch.setenv("BILLING_ENFORCEMENT", "1")


def test_expired_trial_existing_session_generate_402(entitled_then_expired, monkeypatch):
    client, headers, account_id, session_id = entitled_then_expired
    _seed_approved_blueprint(session_id)
    # Live trial can still hit generate (may 400 on blocks — not 402).
    _expire_and_enforce(account_id, monkeypatch)
    res = client.post(
        f"/v1/sessions/{session_id}/product/generate",
        json={},
        headers=headers,
    )
    assert res.status_code == 402, res.text
    assert res.json()["detail"] == "trial_expired"


def test_expired_trial_existing_session_approve_chat_402(
    entitled_then_expired, monkeypatch
):
    client, headers, account_id, session_id = entitled_then_expired
    state = get_session(session_id)
    assert state is not None
    state.product_design.blueprint = {
        "schema": "product_blueprint.v1",
        "product_id": "tasting-room",
        "product_name": "Tasting Room",
        "vertical": "winery",
        "summary": "Tanks and club",
        "capabilities": [],
    }
    state.product_design.blueprint_approved = False
    state.product_design.build_level = "prototype"  # the user's typed choice
    update_session(session_id, state)
    _expire_and_enforce(account_id, monkeypatch)
    res = client.post(
        f"/v1/sessions/{session_id}/chat",
        json={"message": "", "action": "approve"},
        headers=headers,
    )
    assert res.status_code == 402, res.text
    assert res.json()["detail"] == "trial_expired"


def test_expired_trial_can_still_read_product(entitled_then_expired, monkeypatch):
    client, headers, account_id, session_id = entitled_then_expired
    _expire_and_enforce(account_id, monkeypatch)
    res = client.get(f"/v1/sessions/{session_id}/product", headers=headers)
    assert res.status_code == 200, res.text
