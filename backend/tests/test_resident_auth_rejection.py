"""Resident-engineer state-changing routes must reject unauthenticated callers.

Before this, ``/draft-change-request`` and ``/change-request`` had no auth
dependency at all, and ``/heal`` ran as a default "operator" when the steward
auth module was absent. All of them now fail closed (401) without an
authenticated principal.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def client(monkeypatch):
    # Resident entrypoints are flag-gated; enable so we reach the auth check
    # (not the 503 "disabled" gate).
    monkeypatch.setenv("RESIDENT_ENGINEER_ENABLED", "1")
    from app.main import app

    return TestClient(app)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/v1/resident/draft-change-request", {"summary": "x"}),
        ("post", "/v1/resident/change-request", {"product_id": "p", "symptom": "s"}),
        ("post", "/v1/resident/heal", {"action_id": "noop"}),
        ("post", "/v1/resident/heal/approval", {"action_id": "noop"}),
    ],
)
def test_state_changing_routes_reject_unauthenticated(client, method, path, body):
    resp = getattr(client, method)(path, json=body)
    # Must NOT be a 2xx: an unauthenticated caller cannot draft/emit/heal.
    assert resp.status_code == 401, (path, resp.status_code, resp.text)


def test_status_is_public(client):
    # /status is intentionally public (reports flag + allowlist, no execution).
    resp = client.get("/v1/resident/status")
    assert resp.status_code == 200
