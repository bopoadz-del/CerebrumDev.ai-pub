"""Phase 1 acceptance: physical-partition tenant isolation (option B).

The steward kit's isolation seam: one SQLite file + one Chroma collection per
tenant, resolved from the authenticated principal. T1.1-T1.6 + the boot
probe, driven against the real kit modules (loaded under the app.steward
alias the factory tests use for the kit).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

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

B_DISTINCTIVE = "B_ONLY_CORPUS_MARKER_9f3a"


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


def _principal(auth_mod, tenant: str, estate: str = "estate_a"):
    cls = auth_mod.AuthenticatedPrincipal
    try:
        return cls(
            principal_id=f"p-{tenant}",
            display_name="Probe Principal",
            role="admin",
            tenant_id=tenant,
            estate_id=estate,
            estate_role="admin",
            auth_mode="bearer",
        )
    except TypeError:  # pragma: no cover - plain class, not a dataclass
        obj = cls.__new__(cls)
        obj.principal_id = f"p-{tenant}"
        obj.display_name = "Probe Principal"
        obj.role = "admin"
        obj.tenant_id = tenant
        obj.estate_id = estate
        obj.estate_role = "admin"
        obj.auth_mode = "bearer"
        return obj


@pytest.fixture()
def steward_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STEWARD_ALLOW_DEMO_AUTH_BYPASS", "0")
    monkeypatch.setenv("STEWARD_LEGACY_RAG_ENABLED", "0")
    monkeypatch.setenv("STEWARD_ADMIN_ROUTES_ENABLED", "0")
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    return tmp_path


@pytest.fixture()
def auth_mod(steward_env):
    _load_module("errors.py", "steward_errors_ts")
    _load_module("config.py", "steward_config_ts")
    _load_module("models.py", "steward_models_ts")
    _load_module("tenant_store.py", "steward_tenant_store_ts")
    _load_module("auth_models.py", "steward_auth_models_ts")
    _load_module("db.py", "steward_db_ts")
    return _load_module("auth.py", "steward_auth_ts")


@pytest.fixture()
def tenant_store_mod(steward_env, auth_mod):
    return _load_module("tenant_store.py", "steward_tenant_store_ts")


@pytest.fixture()
def kit_db(auth_mod):
    _load_module("config.py", "steward_config_ts")
    _load_module("models.py", "steward_models_ts")
    _load_module("audit_models.py", "steward_audit_models_ts")
    _load_module("migrations_runner.py", "steward_migrations_runner_ts")
    return _load_module("db.py", "steward_db_ts")


# -- T1.3: unauthenticated context has no store -------------------------------


def test_unauthenticated_context_has_no_store(tenant_store_mod):
    """No principal, no store — raw names are refused with a named reason."""
    with pytest.raises(tenant_store_mod.TenantStoreError) as exc:
        tenant_store_mod.resolve_tenant_store(None)
    assert tenant_store_mod.CLIENT_SUPPLIED_STORE_NAME in str(exc.value)

    for raw in ("tenant_a", "tenant_a:estate_a", {"tenant_id": "tenant_a"}):
        with pytest.raises(tenant_store_mod.TenantStoreError) as exc:
            tenant_store_mod.resolve_tenant_store(raw)
        assert tenant_store_mod.CLIENT_SUPPLIED_STORE_NAME in str(exc.value)


def test_unbound_request_has_no_store(tenant_store_mod):
    with pytest.raises(tenant_store_mod.TenantStoreError) as exc:
        tenant_store_mod.current_tenant_store()
    assert tenant_store_mod.NO_AUTHENTICATED_TENANT in str(exc.value)


# -- T1.1: cross-tenant open is impossible, not merely empty ------------------


def test_cross_tenant_open_is_impossible(tenant_store_mod, auth_mod):
    a = _principal(auth_mod, "tenant_a", "estate_a")
    b = _principal(auth_mod, "tenant_b", "estate_b")
    store_a = tenant_store_mod.resolve_tenant_store(a)
    store_b = tenant_store_mod.resolve_tenant_store(b)

    assert store_a.digest != store_b.digest
    assert store_a.sqlite_path != store_b.sqlite_path
    assert store_a.chroma_collection != store_b.chroma_collection
    # The only constructor of B's store is B's principal.
    with pytest.raises(tenant_store_mod.TenantStoreError) as exc:
        tenant_store_mod.resolve_tenant_store("tenant_b")
    assert tenant_store_mod.CLIENT_SUPPLIED_STORE_NAME in str(exc.value)


# -- T1.4: boot probe rejects a client-named seam -----------------------------


def test_boot_probe_accepts_the_real_seam(tenant_store_mod):
    tenant_store_mod.assert_tenant_store_seam()


def test_boot_probe_rejects_client_named_stores(tenant_store_mod, monkeypatch):
    """Patching the seam to accept a raw name refuses the boot (named reason)."""
    monkeypatch.setattr(
        tenant_store_mod, "resolve_tenant_store", lambda candidate: object()
    )
    with pytest.raises(tenant_store_mod.TenantStoreError) as exc:
        tenant_store_mod.assert_tenant_store_seam()
    assert tenant_store_mod.TENANT_STORE_NOT_ADDRESSABLE in str(exc.value)


# -- T1.5: migrate-on-open is explicit and idempotent -------------------------


def test_first_open_migrates_and_reopen_is_idempotent(
    tenant_store_mod, auth_mod, kit_db
):
    from sqlalchemy import inspect

    store = tenant_store_mod.resolve_tenant_store(
        _principal(auth_mod, "tenant_a", "estate_a")
    )
    with kit_db.tenant_session_scope(store) as session:
        tables = inspect(session.get_bind()).get_table_names()
    assert tables, "first open must run the migrations (alembic upgrade head)"
    assert "documents" in tables

    with kit_db.tenant_session_scope(store) as session:
        again = inspect(session.get_bind()).get_table_names()
    assert again == tables, "re-open must be idempotent, not a re-migration"


# -- T1.2: A's retrieval session never sees B's corpus ------------------------


def test_cross_tenant_retrieval_never_leaks(tenant_store_mod, auth_mod, kit_db):
    """T1.2 (Phase 1 scope): A's session can only address A's file.

    The full retrieval-entrypoint variant is Phase 4 (the pg-only engine
    cannot run on a SQLite tenant file yet); Phase 1 proves the boundary
    that test builds on: zero B rows via A's session, not a filter.
    """
    models = sys.modules["app.steward.models"]
    store_a = tenant_store_mod.resolve_tenant_store(
        _principal(auth_mod, "tenant_a", "estate_a")
    )
    store_b = tenant_store_mod.resolve_tenant_store(
        _principal(auth_mod, "tenant_b", "estate_b")
    )

    # Seed B's corpus with a distinctive source record.
    with kit_db.tenant_session_scope(store_b) as session:
        session.add(
            models.SourceRecord(
                id="src-b",
                release_id="rel-1",
                rag_pack_id="pack-b",
                collection_id="col-b",
                source_id="src-b",
                source_name=B_DISTINCTIVE,
                licence="probe-licence",
            )
        )

    # A's session sees ZERO of B's rows — the file boundary, not a filter.
    with kit_db.tenant_session_scope(store_a) as session:
        rows = (
            session.query(models.SourceRecord)
            .filter(models.SourceRecord.source_name == B_DISTINCTIVE)
            .all()
        )
        assert rows == []
        assert session.query(models.SourceRecord).count() == 0

    # Phase 1 hole (named): the retrieval entrypoint itself (hybrid_search)
    # is the Postgres FTS/pgvector engine and cannot run against a SQLite
    # tenant file until Phase 4's kit engine lands. Phase 1 asserts the
    # physical boundary that leak-test will build on: A's session can only
    # ever address A's file, so B's chunks are unreachable, not merely
    # filtered out.


# -- T1.6: backup/restore/delete are tenant-scoped ----------------------------


def test_backup_restore_delete_are_tenant_scoped(tenant_store_mod, auth_mod):
    store_a = tenant_store_mod.resolve_tenant_store(
        _principal(auth_mod, "tenant_a", "estate_a")
    )
    store_b = tenant_store_mod.resolve_tenant_store(
        _principal(auth_mod, "tenant_b", "estate_b")
    )

    # Give B a physical file.
    store_b.storage_root.mkdir(parents=True, exist_ok=True)
    store_b.sqlite_path.write_bytes(b"B")

    # A's backup target lives INSIDE A's own root.
    archive = tenant_store_mod.archive_path_for(store_a)
    assert str(archive).startswith(str(store_a.storage_root))

    # A deletes its own root; B's file is untouched.
    store_a.storage_root.mkdir(parents=True, exist_ok=True)
    tenant_store_mod.delete_tenant_store(store_a)
    assert not store_a.storage_root.exists()
    assert store_b.sqlite_path.read_bytes() == b"B"
