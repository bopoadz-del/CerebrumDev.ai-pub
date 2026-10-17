"""Both emitted suites build their payloads with ONE builder.

Live 2026-10-05 (production build, SAME_FAILURE_TWICE at TESTER):
* ``tests/test_placeholder_contract.py`` posted the Factory's spec sample;
  the route refused it at 422 ("Missing required field: member_id") before
  the declared-placeholder 503 refusal it is the authority on;
* ``tests/test_routes.py`` was refused "Missing required field: <x>_id" on
  three capabilities -- and the model's own default for a required text
  field is "", which the route counts as missing, so a "fill it from the
  model default" retry could not recover either.

The product's models declare required id fields the spec sample lacks. Both
suites now render ``payload_helpers``: the spec sample completed from the
PRODUCT'S OWN model (vocabulary, a real default, else the neutral sample of
the field's type), plus the data-only rejection retry. Everything below is
invented: an invented product with three working capabilities, each
requiring an invented id field whose model default is empty, and one
capability calling a declared placeholder connector.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.factory.build.authority import BuildRole
from app.factory.build.placeholder_connectors import (
    CONTRACT_TEST,
    UNAVAILABLE_KIND,
    UNAVAILABLE_STATUS,
    setting_for,
)
from app.factory.build.roles import RoleContext
from app.factory.build.roles_handlers import run_tester as run_tester_impl
from app.factory.build.store_acceptance import render_auth_module
from app.factory.build.workspace import RoleWorkspace

WORKING = ("kiln_rota", "loom_stock", "quarry_slots")
PLACEHOLDER = "moth_dispatch"
CONNECTOR = "glimmer_relay"
TOKEN = "invented-test-token"


def _spec() -> Dict[str, Any]:
    # The Factory's spec knows only a reference; the product's own model
    # (below) also requires an id field the spec never named.
    return {"fields": [{"name": "reference", "type": "str", "required": True}]}


def _model(cap: str) -> type:
    id_field = cap.split("_")[0] + "_id"
    cls = dataclasses.make_dataclass(
        "M_" + cap,
        [("reference", str, dataclasses.field(default="")),
         (id_field, str, dataclasses.field(default=""))],
    )
    cls.FIELDS = ["reference", id_field]
    cls.CONSTRAINTS = {"reference": {"required": True}, id_field: {"required": True}}
    return cls


ALL = (*WORKING, PLACEHOLDER)


class _Cap:
    def __init__(self, cid, connectors=()):
        self.capability_id = cid
        self.id = cid
        self.block_ids = ()
        self.notes = cid
        self.connectors = list(connectors)


class _Plan:
    def __init__(self, caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Invented Workshop"
    product_id = "invented-workshop"
    vertical = "product"
    connectors = [CONNECTOR]

    def __init__(self, caps):
        self.capabilities = caps


@pytest.fixture
def product(monkeypatch):
    models = types.ModuleType("app.models")
    models.MODELS = {cap: _model(cap) for cap in ALL}
    monkeypatch.setitem(sys.modules, "app.models", models)
    monkeypatch.setenv("PLATFORM_TOKEN", TOKEN)

    auth = types.ModuleType("rendered_auth")
    exec(compile(render_auth_module(), "app/auth.py", "exec"), auth.__dict__)

    app = FastAPI()
    stored: Dict[str, List[Dict[str, Any]]] = {cap: [] for cap in ALL}

    def route(cap):
        async def post(request: Request):
            if request.headers.get("Authorization") != "Bearer " + TOKEN:
                return JSONResponse({"detail": "authentication_required"}, status_code=401)
            payload = await request.json()
            auth.reject_invalid_payload(cap, payload)  # 422 before 503
            if cap == PLACEHOLDER_ROUTE["cap"]:
                return JSONResponse(
                    {"ok": False, "error_kind": UNAVAILABLE_KIND,
                     "settings": [setting_for(CONNECTOR)]},
                    status_code=UNAVAILABLE_STATUS,
                )
            stored[cap].append(payload)
            return {"ok": True}

        async def listing():
            return {"items": stored[cap]}

        app.post(f"/v1/{cap}")(post)
        app.get(f"/v1/{cap}")(listing)

    for cap in ALL:
        route(cap)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app, stored


# Which route plays the declared placeholder is data the test owns, read by
# the invented product above -- the Factory code under test never sees it.
PLACEHOLDER_ROUTE = {"cap": PLACEHOLDER}


def _emit(tmp_path: Path) -> Path:
    caps = [_Cap(c) for c in WORKING] + [_Cap(PLACEHOLDER, [CONNECTOR])]
    ws = RoleWorkspace(BuildRole.TESTER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(caps),
        plan=_Plan(caps),
        state={"model_specs": {c: _spec() for c in ALL}, "vendored_blocks": ()},
    )
    run_tester_impl(ctx)
    return tmp_path / "build" / "tests"


def _load(path: Path, app: FastAPI) -> Dict[str, Any]:
    """Execute an emitted suite with ``app.main.app`` bound to ``app``."""
    main = types.ModuleType("app.main")
    main.app = app
    sys.modules["app.main"] = main
    ns: Dict[str, Any] = {"__name__": "emitted_" + path.stem}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


def test_the_contract_test_reaches_the_declared_refusal(tmp_path, product, monkeypatch):
    app, stored = product
    tests_dir = _emit(tmp_path)
    contract = tests_dir / Path(CONTRACT_TEST).name
    assert contract.is_file()
    monkeypatch.setitem(sys.modules, "app.main", types.ModuleType("app.main"))
    ns = _load(contract, app)
    ns[f"test_{PLACEHOLDER}_answers_the_declared_unavailable_refusal"]()
    assert stored[PLACEHOLDER] == []


def test_every_capability_route_answers_with_model_required_ids(tmp_path, product, monkeypatch):
    app, stored = product
    tests_dir = _emit(tmp_path)
    monkeypatch.setitem(sys.modules, "app.main", types.ModuleType("app.main"))
    ns = _load(tests_dir / "test_routes.py", app)
    ns["test_every_capability_route_answers"]()
    for cap in WORKING:
        assert len(stored[cap]) == 1
        id_field = cap.split("_")[0] + "_id"
        assert stored[cap][0][id_field] not in (None, "")


def test_both_suites_carry_the_same_builder(tmp_path, product):
    tests_dir = _emit(tmp_path)
    routes = (tests_dir / "test_routes.py").read_text(encoding="utf-8")
    contract = (tests_dir / Path(CONTRACT_TEST).name).read_text(encoding="utf-8")
    from app.factory.build.payload_helpers import render_payload_helpers

    block = "\n".join(render_payload_helpers())
    assert block in routes
    assert block in contract
