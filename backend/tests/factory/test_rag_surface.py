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


def test_planted_ingest_accepts_body_and_query_returns_hits(tmp_path, monkeypatch):
    """Keep-path handlers are callable: plant a paragraph, query hits it."""
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
    query = client.get("/v1/rag/query", params={"q": "copper kettle ordinance"})
    assert query.status_code == 200, query.text
    body = query.json()
    assert body["ok"] is True
    assert body["hit_count"] >= 1
    assert body["hits"]
    blob = (query.text or "").lower()
    assert "copper kettle" in blob
    posted = client.post(
        "/v1/rag/query",
        json={"q": "copper kettle ordinance", "query": "copper kettle ordinance"},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["hits"]


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
