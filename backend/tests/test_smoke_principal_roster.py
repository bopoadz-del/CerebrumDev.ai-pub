"""The smoke gate's principal roster: the smoke plus one account per repro.

The release cycle runs repro builds BESIDE the smoke, each on its own verified
account. Two rules make that safe:

1. smoke-login issues as many distinct principals as the cycle asks for (up to
   the declared roster), and refuses -- never trims -- a larger ask.
2. Only the smoke's OWN principal (roster index 0) may use the worker's
   reserved slot. A repro account queues like any user, so a repro can never
   take the slot the smoke depends on. (Until this change BOTH smoke-issued
   accounts were reserved, so the co-op repro on account b held the reserved
   slot whenever it ran beside the smoke.)
"""

from __future__ import annotations

import threading
import time

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
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_S", raising=False)
    monkeypatch.delenv("SMOKE_ACCOUNT_PASSWORD", raising=False)
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "correct-gate-token")

    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    from app.routers import accounts, sessions

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(sessions.router, prefix="/v1/sessions")
    return TestClient(app)


GATE = {"X-Smoke-Gate": "correct-gate-token"}


def test_the_cycle_gets_one_distinct_verified_account_per_run(client):
    res = client.post("/v1/auth/smoke-login", headers=GATE, json={"principals": 3})
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["login_tokens"]) == 3
    assert len(set(body["account_ids"])) == 3, "two runs on one account share its slot"
    # Index 0 is the smoke's own principal, the same as the legacy field.
    assert body["login_tokens"][0] == body["login_token"]
    assert body["account_ids"][0] == body["account_id"]

    # Each is a real, verified principal that can open a session.
    for tok in body["login_tokens"]:
        made = client.post("/v1/sessions/", headers={"Authorization": f"Bearer {tok}"})
        assert made.status_code == 200, made.text


def test_the_same_roster_comes_back_on_every_login(client):
    first = client.post("/v1/auth/smoke-login", headers=GATE, json={"principals": 3}).json()
    again = client.post("/v1/auth/smoke-login", headers=GATE, json={"principals": 3}).json()
    assert first["account_ids"] == again["account_ids"]


def test_no_body_still_issues_the_pair_the_smoke_has_always_used(client):
    body = client.post("/v1/auth/smoke-login", headers=GATE).json()
    assert body["login_token"] != body["login_token_b"]
    assert len(body["login_tokens"]) == 2


@pytest.mark.parametrize("asked", [0, -1, "3", 2.0, True, 99])
def test_an_ask_outside_the_roster_is_refused_not_trimmed(client, asked):
    res = client.post("/v1/auth/smoke-login", headers=GATE, json={"principals": asked})
    assert res.status_code == 400, res.text


def test_the_roster_still_needs_the_gate(client):
    res = client.post(
        "/v1/auth/smoke-login",
        headers={"X-Smoke-Gate": "wrong"},
        json={"principals": 3},
    )
    assert res.status_code == 401


def test_only_the_smokes_own_principal_is_reserved(client, monkeypatch):
    from app.core.trial_limits import (
        SMOKE_PRINCIPALS,
        is_ops_smoke_account,
        is_reserved_smoke_account,
    )
    from app.factory.build.codewhale_worker import tenant_reserved
    from app.factory.build.tenant_bind import bind_tenant_store

    body = client.post(
        "/v1/auth/smoke-login", headers=GATE, json={"principals": len(SMOKE_PRINCIPALS)}
    ).json()
    smoke, *repros = body["account_ids"]

    assert is_reserved_smoke_account(smoke) is True
    assert tenant_reserved(bind_tenant_store(smoke)) is True
    for account in repros:
        # Still a smoke-issued account: quota-exempt, so the cycle can re-run.
        assert is_ops_smoke_account(account) is True
        # But never the reserved slot.
        assert is_reserved_smoke_account(account) is False
        assert tenant_reserved(bind_tenant_store(account)) is False


def test_repros_on_other_accounts_queue_and_never_take_the_smokes_slot(client):
    """The live box: 3 slots, 1 reserved. Two repros fill the two user slots;
    a third repro WAITS even though the reserved slot is free, and the smoke,
    arriving last, starts at once."""
    from app.factory.build.codewhale_worker import (
        InProcessSlotCounter,
        reserved_slots,
        tenant_reserved,
    )
    from app.factory.build.tenant_bind import bind_tenant_store

    body = client.post("/v1/auth/smoke-login", headers=GATE, json={"principals": 4}).json()
    smoke_b, r1_b, r2_b, r3_b = (bind_tenant_store(a) for a in body["account_ids"])
    assert reserved_slots(3) == 1

    counter = InProcessSlotCounter()
    started = {}
    hold = threading.Event()

    def run(name, binding):
        counter.acquire_waiting(
            binding.tenant_key,
            process_cap=3,
            tenant_cap=1,
            reserved=tenant_reserved(binding),
            time_left=lambda: 30.0,
            poll_s=0.01,
        )
        started[name] = time.monotonic()
        hold.wait(10)
        counter.release(binding.tenant_key)

    threads = [
        threading.Thread(target=run, args=("r1", r1_b)),
        threading.Thread(target=run, args=("r2", r2_b)),
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert set(started) == {"r1", "r2"}

    third = threading.Thread(target=run, args=("r3", r3_b))
    third.start()
    time.sleep(0.3)
    assert "r3" not in started, "a repro took the smoke's reserved slot"

    smoke = threading.Thread(target=run, args=("smoke", smoke_b))
    smoke.start()
    deadline = time.monotonic() + 5
    while "smoke" not in started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "smoke" in started, "the smoke waited behind repro builds"
    assert "r3" not in started

    hold.set()
    for t in (*threads, third, smoke):
        t.join(10)
    assert "r3" in started, "the queued repro never ran -- slots must queue, never fail"
    assert counter.snapshot() == {"total": 0, "by_tenant": {}}
