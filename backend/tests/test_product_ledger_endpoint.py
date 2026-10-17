"""GET /v1/sessions/{id}/product/ledger: the whole build ledger, for its owner.

ops.yml ledger-dump classifies a failure from the events themselves; the
build-status summary is not enough (cycle 4: the decisions named the first
finding only). Every string is sanitized as build-status sanitizes it.
"""

from __future__ import annotations

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.core.session_store import create_session
from tests.factory.test_branch_is_platform import _failed_platform

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(12)


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def test_the_owner_reads_every_event(client, tmp_path):
    out, _state = _failed_platform(tmp_path, "sess_ledger_read")
    BuildLedger(out / "build_ledger.jsonl").append(
        EventKind.NOTE, role=BuildRole.WRITER,
        detail="writer echoed Authorization: Bearer " + _GENERATED_KEY_1 + "",
    )
    r = client.get("/v1/sessions/sess_ledger_read/product/ledger")
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    kinds = [e["kind"] for e in events]
    assert EventKind.RUN_FAILED.value in kinds
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert _GENERATED_KEY_1 not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_a_session_with_no_build_has_no_ledger(client):
    create_session("sess_ledger_none", "tester")
    r = client.get("/v1/sessions/sess_ledger_none/product/ledger")
    assert r.status_code == 404


def test_an_unknown_session_is_404(client):
    assert client.get("/v1/sessions/sess_nobody_here/product/ledger").status_code == 404
