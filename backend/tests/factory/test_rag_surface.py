"""Factory-grounded RAG keep-path: real callable ingest/query, not stubs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.factory.build.deploy import render_main
from app.factory.build.rag_surface import (
    FACTORY_GROUNDED_RAG_SOURCE,
    RAG_ROUTES_REL,
    RAG_WIRE_MARK,
    emit_factory_grounded_rag_surface,
    wire_rag_router,
)
from app.factory.build.writer_phases import WRITER_PHASE_FRONTEND_RAG
from tests.factory.test_writer_phases import (
    _Blueprint,
    _Cap,
    _Plan,
    _compiled_steward_dual_rag,
    _plant_quoted_routes,
)

ROOT = Path(__file__).resolve().parents[2]


def _load_planted_router(path: Path):
    spec = importlib.util.spec_from_file_location("factory_grounded_rag_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_planted_ingest_stores_and_query_is_repointed_to_real_answer(tmp_path, monkeypatch):
    """Ingest persists; the query path is RE-POINTED (F-1d) to the real answer
    path, so it is tenant-scoped — an unbound caller is refused by name (401),
    never served an open keyword match. The behavioural citation proof is in
    tests/factory/test_f1d_golden_citation.py."""
    compiled = _compiled_steward_dual_rag()
    planted = emit_factory_grounded_rag_surface(tmp_path, compiled)
    assert planted == ["rag_ingest", "rag_query"]
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    module = _load_planted_router(tmp_path / RAG_ROUTES_REL)
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)
    marker = "ACCEPTANCE-PLANT-copper kettle ordinance"
    ingest = client.post(
        "/v1/rag/ingest",
        json={
            "text": marker,
            "content": marker,
            "paragraph": marker,
            "title": "kettle ordinance",
        },
    )
    assert ingest.status_code == 200, ingest.text
    payload = ingest.json()
    assert payload["ok"] is True
    assert payload["stored"] is True
    assert payload["document_id"]
    assert payload["source"] == FACTORY_GROUNDED_RAG_SOURCE
    # The re-pointed query is tenant-scoped and reaches the REAL answer path.
    import sys
    import types

    from app.cerebrum_product_kernel.ingestion import retrieval_index

    tenancy = types.ModuleType("app.tenancy")

    class _TenantRefused(Exception):
        pass

    class _Tenant:
        def __init__(self, tid):
            self.tenant_id = tid

    def _resolve(headers):
        auth = (headers or {}).get("authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if token != "good-token":
            raise _TenantRefused(token)
        return _Tenant("tenant-a")

    tenancy.TenantRefused = _TenantRefused
    tenancy.resolve_tenant = _resolve
    monkeypatch.setitem(sys.modules, "app.tenancy", tenancy)
    retrieval_index.set_engine_provider(None)  # no engine installed here

    # No auth -> 401 by name. The old keyword matcher answered 200 with open
    # "hits"; a tenant-scoped refusal proves the open matcher is retired.
    unbound = client.get("/v1/rag/query", params={"q": "copper kettle ordinance"})
    assert unbound.status_code == 401, unbound.text
    assert unbound.json()["detail"] == "authentication_required"
    # Bound, but no retrieval engine installed -> the REAL answer path's
    # computed 503 "retrieval backend not configured" (never a keyword 200).
    bound = client.get(
        "/v1/rag/query",
        params={"q": "copper kettle ordinance"},
        headers={"Authorization": "Bearer good-token"},
    )
    assert bound.status_code == 503, bound.text
    assert retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED in bound.json()["detail"]
    # The re-pointed module no longer ships the keyword-overlap matcher.
    assert not hasattr(module, "search_documents")
    assert not hasattr(module, "score_document")


def test_planted_ingest_rejects_empty_body(tmp_path, monkeypatch):
    compiled = _compiled_steward_dual_rag()
    emit_factory_grounded_rag_surface(tmp_path, compiled)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    module = _load_planted_router(tmp_path / RAG_ROUTES_REL)
    app = FastAPI()
    app.include_router(module.router)
    empty = TestClient(app).post("/v1/rag/ingest", json={})
    assert empty.status_code == 200
    assert empty.json()["ok"] is False


def test_render_main_wires_rag_router_without_v1_prefix():
    text = render_main("Steward")
    assert RAG_WIRE_MARK in text
    assert "app.include_router(rag_router)" in text
    assert 'app.include_router(router, prefix="/v1")' in text


def test_wire_rag_router_appends_include_when_main_exists(tmp_path):
    main = tmp_path / "app" / "main.py"
    main.parent.mkdir(parents=True)
    main.write_text(
        "from fastapi import FastAPI\n"
        "from app.routes import router\n"
        'app = FastAPI()\n'
        'app.include_router(router, prefix="/v1")\n',
        encoding="utf-8",
    )
    assert wire_rag_router(tmp_path) is True
    text = main.read_text(encoding="utf-8")
    assert RAG_WIRE_MARK in text
    assert wire_rag_router(tmp_path) is False


def test_writer_later_phase_loop_plants_before_accept():
    """Join: RoleRunner PHASE 2 keep-path runs before accept_writer_phase."""
    src = (
        ROOT
        / "app"
        / "factory"
        / "build"
        / "roles_handlers.py"
    ).read_text(encoding="utf-8")
    plant_at = src.index("emit_factory_grounded_rag_surface")
    accept_at = src.index("accept_writer_phase(ctx, phase_id, compiled_brief)")
    assert plant_at < accept_at
    assert "WRITER_PHASE_FRONTEND_RAG" in src
    assert "FACTORY_GROUNDED_RAG_SOURCE" in src


def test_keep_path_skips_when_quoted_steward_routes_already_landed(tmp_path):
    compiled = _compiled_steward_dual_rag()
    _plant_quoted_routes(
        tmp_path / "app" / "steward" / "api.py",
        "/v1/steward/rag/ingest",
        "/v1/steward/rag/query",
    )
    assert emit_factory_grounded_rag_surface(tmp_path, compiled) == []
    assert not (tmp_path / RAG_ROUTES_REL).exists()


def test_keep_path_skips_non_rag_inventory(tmp_path):
    from app.factory.build.brief_compiler import compile_brief

    # No bound block declares the retrieval read, so there is no RAG surface
    # to plant -- whatever the capability is called.
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("patient_records_management", ["database"], "REUSE")),
        store_ids={"database"},
    )
    assert emit_factory_grounded_rag_surface(tmp_path, compiled) == []
    assert not (tmp_path / RAG_ROUTES_REL).exists()


def test_phase_constant_matches_live_check_name():
    assert WRITER_PHASE_FRONTEND_RAG == "frontend_rag"
