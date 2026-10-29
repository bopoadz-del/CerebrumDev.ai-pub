"""F-10b-pin: the product ingestion router is REALLY mounted by the rendered
product entrypoint and serves at its own prefix, with no extra dependency.

The rendered ``main.py`` text is executed with the product-side modules it
imports stubbed, then an HTTP request goes through FastAPI. An AST/substring
check cannot tell ``if False: app.include_router(...)``, a re-prefixed mount or
a mount with an always-failing dependency from the real mount; a request can.
Two verticals, two product names: the served app carries the given name.
"""

from __future__ import annotations

import sys
import types
from typing import Dict

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import app.factory.inventory as inventory
from app.factory.build.deploy import render_main

INGESTION_MOD = "app.cerebrum_product_kernel.ingestion.router"


def _stub_modules(probe: APIRouter) -> Dict[str, types.ModuleType]:
    health = types.ModuleType("app.health")
    health.health_response = lambda: {"ok": True}
    observe = types.ModuleType("app.observe")
    observe.install_observability = lambda app: None
    observe.configure_logging = lambda: None
    routes = types.ModuleType("app.routes")
    routes.router = APIRouter()
    ingestion = types.ModuleType(INGESTION_MOD)
    ingestion.product_router = probe
    return {"app.health": health, "app.observe": observe, "app.routes": routes, INGESTION_MOD: ingestion}


def _probe_router() -> APIRouter:
    # Same prefix the kernel's product_router declares; the mount must not
    # re-prefix or wrap it.
    r = APIRouter(prefix="/v1/product/ingestion")

    @r.get("/upload/{job_id}")
    def status(job_id: str) -> dict:
        return {"job_id": job_id, "served": True}

    return r


def _served_app(monkeypatch, *, vertical: str, name: str, excluded: bool):
    monkeypatch.setattr(inventory, "vertical_is_excluded", lambda v, store_root=None: excluded)
    text = render_main(name, vertical)
    for mod_name, mod in _stub_modules(_probe_router()).items():
        monkeypatch.setitem(sys.modules, mod_name, mod)
    ns: dict = {"__name__": "rendered_main_" + vertical}
    exec(compile(text, f"<rendered main {vertical}>", "exec"), ns)  # noqa: S102
    return ns["app"]


@pytest.mark.parametrize(
    "vertical, name, job",
    [("fleet_maintenance", "Fleet One Platform", "job-a1"), ("hotel_operations", "Hotel Two Platform", "job-b2")],
)
def test_non_excluded_vertical_serves_the_ingestion_route(monkeypatch, vertical, name, job):
    app = _served_app(monkeypatch, vertical=vertical, name=name, excluded=False)
    assert app.title == name
    resp = TestClient(app).get(f"/v1/product/ingestion/upload/{job}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"job_id": job, "served": True}


@pytest.mark.parametrize("vertical, name", [("medical", "Med Platform"), ("legal", "Legal Platform")])
def test_excluded_vertical_does_not_serve_the_ingestion_route(monkeypatch, vertical, name):
    app = _served_app(monkeypatch, vertical=vertical, name=name, excluded=True)
    assert TestClient(app).get("/v1/product/ingestion/upload/job-x").status_code == 404
