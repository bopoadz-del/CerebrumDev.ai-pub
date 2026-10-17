"""Steward pilot auth/authz unit tests (Factory kit)."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


ROOT = Path(__file__).resolve().parents[3]
KIT = (_estate_kit()
    / "steward_runtime")


def _ensure_pkg(name: str) -> None:
    if name not in sys.modules:
        mod = types.ModuleType(name)
        if name.startswith("app."):
            mod.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = mod


def _load_module(relative: str, as_name: str):
    for pkg in ("app", "app.steward"):
        _ensure_pkg(pkg)
    path = KIT / relative
    spec = importlib.util.spec_from_file_location(as_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[as_name] = mod
    short = relative.replace(".py", "").replace("/", ".")
    sys.modules[f"app.steward.{short.split('.')[-1]}"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def config_mod(monkeypatch):
    monkeypatch.setenv("STEWARD_ALLOW_DEMO_AUTH_BYPASS", "0")
    monkeypatch.setenv("STEWARD_LEGACY_RAG_ENABLED", "0")
    monkeypatch.setenv("STEWARD_ADMIN_ROUTES_ENABLED", "0")
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    return _load_module("config.py", f"steward_cfg_{id(monkeypatch)}")


@pytest.fixture()
def auth_mod(config_mod, monkeypatch):
    monkeypatch.setenv("STEWARD_TOKEN_PEPPER", "test-pepper")
    errors = _load_module("errors.py", f"steward_err_{id(monkeypatch)}")
    sys.modules["app.steward.errors"] = errors
    sys.modules["app.steward.config"] = config_mod
    models = _load_module("models.py", f"steward_models_{id(monkeypatch)}")
    sys.modules["app.steward.models"] = models
    auth_models = _load_module("auth_models.py", f"steward_auth_models_{id(monkeypatch)}")
    sys.modules["app.steward.auth_models"] = auth_models
    db_stub = types.ModuleType("app.steward.db")

    def _noop_init(*_a, **_k):
        return None

    db_stub.init_engine = _noop_init  # type: ignore[attr-defined]
    db_stub.get_session = lambda: iter([MagicMock()])  # type: ignore[attr-defined]
    sys.modules["app.steward.db"] = db_stub
    return _load_module("auth.py", f"steward_auth_{id(monkeypatch)}")


def test_hash_token_is_deterministic(auth_mod):
    assert auth_mod.hash_token("abc") == auth_mod.hash_token("abc")
    assert auth_mod.hash_token("abc") != auth_mod.hash_token("def")


def test_resolve_principal_requires_bearer_when_bypass_off(auth_mod, config_mod):
    session = MagicMock()
    with pytest.raises(Exception) as exc:
        auth_mod.resolve_principal(
            session,
            authorization=None,
            x_tenant_id="tenant_a",
            x_estate_id="estate_a",
        )
    assert getattr(exc.value, "status_code", None) == 401


def test_demo_bypass_defaults_disabled(config_mod):
    cfg = config_mod.get_config()
    assert cfg.allow_demo_auth_bypass is False
    assert cfg.legacy_rag_enabled is False
    assert cfg.admin_routes_enabled is False


def test_demo_bypass_allows_scope(auth_mod, config_mod, monkeypatch):
    monkeypatch.setenv("STEWARD_ALLOW_DEMO_AUTH_BYPASS", "1")
    cfg = config_mod.StewardRagConfig()
    assert cfg.allow_demo_auth_bypass is True
    session = MagicMock()
    principal = auth_mod.resolve_principal(
        session,
        authorization=None,
        x_tenant_id=None,
        x_estate_id=None,
    )
    assert principal.auth_mode == "demo_bypass"
    assert principal.tenant_id == "tenant_a"


def test_authorize_estate_denies_cross_estate(auth_mod):
    principal = auth_mod.AuthenticatedPrincipal(
        principal_id="p1",
        display_name="Test",
        role="pilot",
        tenant_id="tenant_a",
        estate_id="estate_a",
        estate_role="viewer",
        auth_mode="bearer",
    )
    session = MagicMock()
    with pytest.raises(Exception) as exc:
        auth_mod.authorize_estate(
            session,
            principal=principal,
            tenant_id="tenant_b",
            estate_id="estate_b",
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_readiness_fails_when_bypass_on(config_mod, monkeypatch):
    monkeypatch.setenv("STEWARD_ALLOW_DEMO_AUTH_BYPASS", "1")
    sys.modules["app.steward.config"] = config_mod
    sys.modules["app.steward"] = types.ModuleType("app.steward")
    sys.modules["app.steward"].STEWARD_RAG_VERSION = "test"  # type: ignore[attr-defined]
    db_stub = types.ModuleType("app.steward.db")
    db_stub.init_engine = lambda *_a, **_k: None  # type: ignore[attr-defined]
    db_stub.get_engine = lambda: (_ for _ in ()).throw(RuntimeError("no db"))  # type: ignore[attr-defined]
    sys.modules["app.steward.db"] = db_stub
    embed_stub = types.ModuleType("app.steward.embeddings")
    embed_stub.get_embedder = lambda _cfg: types.SimpleNamespace(  # type: ignore[attr-defined]
        backend_name="hash", fingerprint="hash:test:384"
    )
    sys.modules["app.steward.embeddings"] = embed_stub
    probes = _load_module("probes.py", f"steward_probes_{id(monkeypatch)}")
    payload = probes.readiness_checks()
    bypass_check = next(c for c in payload["checks"] if c["name"] == "auth_bypass_disabled")
    assert bypass_check["ok"] is False
    assert payload["ready"] is False


def test_token_lookup_rejects_revoked(auth_mod):
    token_row = MagicMock()
    token_row.revoked_at = datetime.now(timezone.utc)
    token_row.expires_at = None
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = token_row
    assert auth_mod._lookup_token(session, "raw") is None


def test_token_lookup_rejects_expired(auth_mod):
    token_row = MagicMock()
    token_row.revoked_at = None
    token_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = token_row
    assert auth_mod._lookup_token(session, "raw") is None
