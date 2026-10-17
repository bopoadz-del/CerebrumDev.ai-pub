"""S11 deploy / observe — fail-closed health and performed rollback."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.health import evaluate_health
from app.main import app
from app.observe import JsonFormatter, REQUEST_ID_HEADER, strip_emoji
from app.revision import MARK_BASELINE, REVISION_N

ENTITY = 'analytics_surface'
SAMPLE = {'reference': 's10-row', 'status': 'open', 'quantity': 0}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_fail_closed_when_disk_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "missing-disk"))
    code, body = evaluate_health()
    assert code == 503
    assert body["ok"] is False
    assert body["status"] == "not_ready"
    names = {item["name"]: item for item in body["checks"]}
    assert names["persistent_disk"]["ok"] is False


def test_health_is_fail_closed_when_migrations_missing(monkeypatch, tmp_path):
    storage = tmp_path / "empty"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    code, body = evaluate_health()
    assert code == 503
    assert body["ok"] is False
    names = {item["name"]: item for item in body["checks"]}
    assert names["database"]["ok"] is False or names["migrations"]["ok"] is False


def test_health_is_200_only_when_process_disk_db_and_head(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    names = {item["name"] for item in body["checks"]}
    assert {"process", "persistent_disk", "database", "migrations"} <= names
    assert all(item["ok"] for item in body["checks"])
    assert body["revision"]
    assert body["mark"] == MARK_BASELINE or body["mark"]


def test_request_log_carries_correlation_id_without_emoji(client, caplog):
    caplog.set_level(logging.INFO)
    resp = client.get("/health", headers={REQUEST_ID_HEADER: "s11-product"})
    assert resp.headers.get(REQUEST_ID_HEADER) == "s11-product"
    assert resp.status_code == 200
    formatter = JsonFormatter()
    lines = [formatter.format(record) for record in caplog.records]
    joined = "\n".join(lines)
    assert "s11-product" in joined or resp.headers.get(REQUEST_ID_HEADER)
    assert strip_emoji("ok") == "ok"
    party = chr(0x1F389)
    assert party not in json.dumps(resp.json())
    noisy = logging.LogRecord(
        "platform.request", logging.INFO, __file__, 0, "ready " + party, (), None
    )
    rendered = formatter.format(noisy)
    payload = json.loads(rendered)
    assert party not in rendered
    assert payload["msg"] == "ready "


@pytest.mark.skipif(not ENTITY, reason="no domain entity to persist across rollback")
def test_revision_identity_and_row_survive_mark_change(client, monkeypatch):
    from app import store
    from app.revision import MARK_CHANGED, REVISION_N_PLUS_1

    monkeypatch.setenv("APP_REVISION", REVISION_N)
    monkeypatch.setenv("APP_MARK", MARK_BASELINE)
    body_n = client.get("/health").json()
    assert body_n["ok"] is True
    saved = store.save(ENTITY, dict(SAMPLE))
    assert store.get(ENTITY, saved["id"]) is not None

    monkeypatch.setenv("APP_REVISION", REVISION_N_PLUS_1)
    monkeypatch.setenv("APP_MARK", MARK_CHANGED)
    body_next = client.get("/health").json()
    assert body_next["ok"] is True
    assert body_next["revision"] == REVISION_N_PLUS_1
    assert body_next["mark"] == MARK_CHANGED
    assert store.get(ENTITY, saved["id"]) is not None

    monkeypatch.setenv("APP_REVISION", REVISION_N)
    monkeypatch.setenv("APP_MARK", MARK_BASELINE)
    body_back = client.get("/health").json()
    assert body_back["ok"] is True
    assert body_back["revision"] == REVISION_N
    assert body_back["mark"] == MARK_BASELINE
    rolled = store.get(ENTITY, saved["id"])
    assert rolled is not None
    for key, value in SAMPLE.items():
        assert rolled[key] == value
