"""F-10b: F-1d render_main / product-router / rag re-point pins, and the F-3
platform_packager CLI domain default. Two inputs, two results, everywhere.

* render_main, non-excluded vertical: the entrypoint imports ``product_router``
  ONLY (never the full ``router`` or ``_scaffold_router``), and its lifespan
  calls ``install_default_engine``. Excluded vertical: neither.
* product_router mounts no /drive or /formulas route (the 501 scaffold does),
  and carries GET /upload/{job_id}.
* /v1/rag/query (planted keep-path): GET hands q + answer_text to the REAL
  answer helper and returns its answer; POST with an empty query is 422 and
  never reaches the helper.
* platform_packager CLI config: domain = NO_VERTICAL when CEREBRUM_DOMAIN_KITS
  is unset, the env value when set.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.factory.inventory as inventory
from app.factory.build.deploy import render_main


# ── render_main ────────────────────────────────────────────────────────────

def _render(monkeypatch, *, excluded: bool, vertical: str) -> str:
    seen: List[str] = []

    def fake_excluded(v, store_root=None):
        seen.append(v)
        return excluded

    monkeypatch.setattr(inventory, "vertical_is_excluded", fake_excluded)
    text = render_main("Some Product", vertical)
    assert seen == [vertical], "render_main must ask about the vertical it was given"
    return text


def _ingestion_imports(tree: ast.AST) -> List[str]:
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.cerebrum_product_kernel.ingestion.router":
            names.extend(a.name for a in node.names)
    return names


def _lifespan_calls(tree: ast.AST) -> List[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return [
                c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            ]
    raise AssertionError("rendered main has no lifespan")


@pytest.mark.parametrize("vertical", ["vert_one", "vert_two"])
def test_non_excluded_vertical_mounts_product_router_only_and_installs_engine(monkeypatch, vertical):
    tree = ast.parse(_render(monkeypatch, excluded=False, vertical=vertical))
    assert _ingestion_imports(tree) == ["product_router"]
    assert "install_default_engine" in _lifespan_calls(tree)


@pytest.mark.parametrize("vertical", ["vert_one", "vert_two"])
def test_excluded_vertical_mounts_no_ingestion_and_installs_no_engine(monkeypatch, vertical):
    text = _render(monkeypatch, excluded=True, vertical=vertical)
    tree = ast.parse(text)
    assert _ingestion_imports(tree) == []
    assert "install_default_engine" not in _lifespan_calls(tree)
    assert "install_default_engine" not in text


# ── product_router surface ─────────────────────────────────────────────────

def _paths(r) -> Dict[str, set]:
    app = FastAPI()
    app.include_router(r)
    out: Dict[str, set] = {}
    # Read the mounted surface from OpenAPI: included routers are lazy in this
    # FastAPI, so app.routes does not flatten them.
    for path, ops in app.openapi()["paths"].items():
        out.setdefault(path, set()).update(m.upper() for m in ops)
    return out


def test_product_router_has_no_501_scaffold_routes_but_full_router_does():
    from app.cerebrum_product_kernel.ingestion.router import product_router, router

    product = _paths(product_router)
    full = _paths(router)
    assert not [p for p in product if "/drive" in p or "/formulas" in p]
    assert [p for p in full if "/drive" in p] and [p for p in full if "/formulas" in p]
    assert "GET" in product["/v1/product/ingestion/upload/{job_id}"]
    assert "POST" in product["/v1/product/ingestion/upload"]
    assert "POST" in product["/v1/product/ingestion/answer"]


# ── /v1/rag/query re-point ─────────────────────────────────────────────────

@pytest.fixture
def rag_client(tmp_path, monkeypatch):
    from app.factory.build import rag_surface
    from app.factory.build.rag_surface import RAG_ROUTES_REL

    # Plant the exact source emit_factory_grounded_rag_surface writes. Its
    # rag gate reads signed Store manifests (blocks_declaring_read), which a
    # checkout without the Store cannot answer; the gate is not under test here.
    dest = tmp_path / RAG_ROUTES_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rag_surface._RAG_ROUTES_PY, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("f10b_rag_routes", tmp_path / RAG_ROUTES_REL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    tenancy = types.ModuleType("app.tenancy")

    class _Refused(Exception):
        pass

    def _resolve(headers):
        auth = (headers or {}).get("authorization", "")
        if not auth.startswith("Bearer "):
            raise _Refused()
        return types.SimpleNamespace(tenant_id="tenant-" + auth[7:].strip())

    tenancy.TenantRefused = _Refused
    tenancy.resolve_tenant = _resolve
    monkeypatch.setitem(sys.modules, "app.tenancy", tenancy)

    calls: List[Any] = []
    import app.cerebrum_product_kernel.ingestion.router as ingestion_router

    def spy(tenant_id, req):
        calls.append((tenant_id, req.query, req.answer_text))
        return {"kind": "improvised", "text": req.answer_text, "query": req.query}

    monkeypatch.setattr(ingestion_router, "answer_question", spy)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app), calls


@pytest.mark.parametrize(
    "token, q, answer",
    [("alpha", "what is the lease term", "The lease term is 3 years."),
     ("beta", "who signs the deed", "The registrar signs.")],
)
def test_rag_get_reaches_real_answer_and_returns_answer_text(rag_client, token, q, answer):
    client, calls = rag_client
    resp = client.get("/v1/rag/query", params={"q": q, "answer_text": answer},
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert calls == [("tenant-" + token, q, answer)]
    assert resp.json()["text"] == answer
    assert resp.json()["query"] == q


@pytest.mark.parametrize("body", [{}, {"q": "  ", "answer_text": "x"}])
def test_rag_post_empty_query_is_422_and_never_reaches_answer(rag_client, body):
    client, calls = rag_client
    resp = client.post("/v1/rag/query", json=body, headers={"Authorization": "Bearer alpha"})
    assert resp.status_code == 422
    assert calls == []


def test_rag_post_nonempty_query_reaches_answer(rag_client):
    client, calls = rag_client
    resp = client.post("/v1/rag/query", json={"query": "boundary survey", "answer_text": "ok"},
                       headers={"Authorization": "Bearer gamma"})
    assert resp.status_code == 200, resp.text
    assert calls == [("tenant-gamma", "boundary survey", "ok")]


# ── F-3: platform_packager CLI domain default ──────────────────────────────

def _cli_domain(tmp_path: Path, env_vars: Dict[str, str]) -> str:
    from app.core.platform_packager import _drop_cli_artifacts

    engine = tmp_path / "engine"
    (engine / "cli").mkdir(parents=True)
    (engine / "cli" / "marker.txt").write_text("x", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _drop_cli_artifacts(pkg, "svc", env_vars, engine)
    for line in (pkg / "cli" / "config.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("domain = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("config.toml has no domain line")


def test_cli_domain_is_no_vertical_when_env_unset(tmp_path):
    from app.factory.store_kits import NO_VERTICAL

    assert _cli_domain(tmp_path, {}) == NO_VERTICAL


@pytest.mark.parametrize("kit", ["kit_alpha", "kit_beta"])
def test_cli_domain_is_the_env_value_when_set(tmp_path, kit):
    assert _cli_domain(tmp_path, {"CEREBRUM_DOMAIN_KITS": kit}) == kit
