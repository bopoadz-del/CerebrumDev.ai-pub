"""The owner can switch the coder model and reboot from the admin page.

When the configured coding model stops responding, the only lever used to be
an env var on ECS the owner could not reach. These tests pin that the override
wins, survives, and is master-key gated.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    import app.core.runtime_settings as rs

    importlib.reload(rs)
    return rs


def test_the_override_wins_over_env_default(storage, monkeypatch):
    from app.factory import code_cli

    monkeypatch.delenv("DEEPSEEK_CODE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    # default with no override
    base = code_cli.deepseek_code_model()
    # operator sets a model
    storage.set_value(storage.CODER_MODEL, "deepseek-2.0")
    assert code_cli.deepseek_code_model() != base
    assert "2.0" in code_cli.deepseek_code_model()


def test_a_blank_value_clears_the_override(storage, monkeypatch):

    monkeypatch.delenv("DEEPSEEK_CODE_MODEL", raising=False)
    storage.set_value(storage.CODER_MODEL, "deepseek-2.0")
    storage.set_value(storage.CODER_MODEL, "")  # clear
    assert storage.get(storage.CODER_MODEL) is None


def test_the_provider_override_wins(storage, monkeypatch):
    from app.factory.build import codewhale_worker

    monkeypatch.delenv("CODEWHALE_PROVIDER", raising=False)
    assert codewhale_worker.worker_provider() == "deepseek"
    storage.set_value(storage.CODER_PROVIDER, "openrouter")
    assert codewhale_worker.worker_provider() == "openrouter"


def test_an_unknown_key_is_refused(storage):
    with pytest.raises(ValueError):
        storage.set_value("not_a_setting", "x")


def test_set_and_reboot_require_the_master_key(storage, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import admin_ops

    app = FastAPI()
    app.include_router(admin_ops.router, prefix="/v1/admin")
    client = TestClient(app)

    # no key -> rejected
    assert client.post("/v1/admin/coder-model", json={"model": "x"}).status_code in (401, 404)
    assert client.post("/v1/admin/reboot").status_code in (401, 404)

    # with the master key -> set works and the override lands
    h = {"Authorization": "Bearer master-secret"}
    r = client.post("/v1/admin/coder-model", json={"model": "deepseek-2.0"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["overrides"].get("coder_model") == "deepseek-2.0"
    # GET reflects it
    g = client.get("/v1/admin/coder-model", headers=h)
    assert g.status_code == 200 and "deepseek" in g.json()["effective"]["model"]
