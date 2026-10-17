"""The route suite learns accepted values from the rejection AS DATA.

Live 2026-10-05 (production build, SAME_FAILURE_TWICE at TESTER): the
emitted ``tests/test_routes.py`` ran a vocabulary regex over the raw 422
body. A key ``"allowed_scopes":`` in that JSON matched, the value
``_scopes":`` was written into ``reference`` and ``status``, and the test
oscillated between it and the real vocabulary until the run died.

Both sides are rendered from ``rejection_contract``: the product's route
guard states (field, reason, allowed) as headers, the handler guard as body
keys, and the emitted test reads only those. The capability, its fields and
its vocabulary below are invented; nothing here names a real product.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.factory.build.authority import BuildRole
from app.factory.build.rejection_contract import (
    ALLOWED_VALUES_HEADER,
    ALLOWED_VALUES_KEY,
    MISSING_REQUIRED,
    NOT_ALLOWED,
    REJECTED_FIELD_HEADER,
    REJECTED_FIELD_KEY,
    REJECTION_REASON_HEADER,
    REJECTION_REASON_KEY,
)
from app.factory.build.roles import RoleContext
from app.factory.build.roles_handlers import _constraint_guard
from app.factory.build.roles_handlers import run_tester as run_tester_impl
from app.factory.build.store_acceptance import render_auth_module
from app.factory.build.workspace import RoleWorkspace

CAP = "lumen_ledger"
SPEC = {
    "fields": [
        {"name": "reference", "type": "str", "required": True},
        {"name": "phase", "type": "str", "required": True,
         "allowed_values": ["sprout", "bloom"]},
    ]
}


@dataclasses.dataclass
class _LumenLedger:
    reference: str = "R-1"
    phase: str = "sprout"
    FIELDS = ["reference", "phase"]
    CONSTRAINTS = {
        "reference": {"required": True},
        "phase": {"required": True, "allowed_values": ["sprout", "bloom"]},
    }


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = ()
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Invented Probe"
    product_id = "invented-probe"
    vertical = "product"
    connectors: List[str] = []
    capabilities: List[Any] = []


@pytest.fixture
def product_models(monkeypatch):
    """The product's app.models, as the Factory renders it: MODELS maps a
    capability id to a dataclass whose defaults satisfy its constraints."""
    mod = types.ModuleType("app.models")
    mod.MODELS = {CAP: _LumenLedger}
    monkeypatch.setitem(sys.modules, "app.models", mod)
    return mod


def _emitted_routes(tmp_path: Path) -> str:
    ws = RoleWorkspace(BuildRole.TESTER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(_Cap(CAP)),
        state={"model_specs": {CAP: SPEC}, "vendored_blocks": ()},
    )
    run_tester_impl(ctx)
    return (tmp_path / "build" / "tests" / "test_routes.py").read_text(encoding="utf-8")


def _helpers(source: str, client: Any) -> Dict[str, Any]:
    """The emitted rejection helpers, bound to ``client`` -- exactly the
    code the product's route suite runs."""
    start = source.index("import dataclasses as _dataclasses")
    end = source.index("def test_health")
    ns: Dict[str, Any] = {"client": client}
    exec(compile(source[start:end], "test_routes.py", "exec"), ns)
    return ns


def _auth_module() -> types.ModuleType:
    mod = types.ModuleType("rendered_auth")
    exec(compile(render_auth_module(), "app/auth.py", "exec"), mod.__dict__)
    return mod


def _app(extra_body: Dict[str, Any] | None = None) -> FastAPI:
    """A product route guarded by the REAL emitted reject_invalid_payload."""
    auth = _auth_module()
    app = FastAPI()

    @app.post(f"/v1/{CAP}")
    async def _post(request: Request):
        payload = await request.json()
        auth.reject_invalid_payload(CAP, payload)
        return {"ok": True, **(extra_body or {})}

    return app


def test_the_emitted_suite_carries_no_prose_parser(tmp_path, product_models):
    source = _emitted_routes(tmp_path)
    assert "_ALLOWED_RE" not in source
    assert "import re as _re" not in source
    for constant in (REJECTED_FIELD_HEADER, REJECTION_REASON_HEADER,
                     ALLOWED_VALUES_HEADER, REJECTED_FIELD_KEY):
        assert constant in source


def test_the_route_guard_states_the_rejection_as_data(product_models):
    auth = _auth_module()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        auth.reject_invalid_payload(CAP, {"reference": "x", "phase": "wilt"})
    headers = exc.value.headers
    assert headers[REJECTION_REASON_HEADER] == NOT_ALLOWED
    assert headers[REJECTED_FIELD_HEADER] == '"phase"'
    assert headers[ALLOWED_VALUES_HEADER] == '["sprout", "bloom"]'

    with pytest.raises(HTTPException) as exc:
        auth.reject_invalid_payload(CAP, {"phase": "bloom"})
    assert exc.value.headers[REJECTION_REASON_HEADER] == MISSING_REQUIRED
    assert exc.value.headers[REJECTED_FIELD_HEADER] == '"reference"'


def test_a_closed_vocabulary_is_adopted_in_the_named_field_only(tmp_path, product_models):
    """The body also carries a key that the old regex read as a value."""
    client = TestClient(_app())
    ns = _helpers(_emitted_routes(tmp_path), client)
    resp, corr = ns["_post_accepting"](
        f"/v1/{CAP}", {"reference": "keep-me", "phase": "wilt"}, {}, CAP
    )
    assert resp.status_code == 200, resp.text
    assert corr == ["phase='wilt'->'sprout'"]


def test_a_missing_required_field_is_filled_from_the_product_model(tmp_path, product_models):
    """Completed from the model BEFORE the first post: no refusal needed."""
    client = TestClient(_app())
    ns = _helpers(_emitted_routes(tmp_path), client)
    resp, corr = ns["_post_accepting"](f"/v1/{CAP}", {"phase": "bloom"}, {}, CAP)
    assert resp.status_code == 200, resp.text
    assert corr == []


def test_a_missing_required_rejection_is_corrected_from_the_model(tmp_path, product_models):
    """A field the model does not mark required but the route refuses as
    missing is filled from the model's own declaration and retried."""
    headers = {REJECTED_FIELD_HEADER: '"reference"', REJECTION_REASON_HEADER: MISSING_REQUIRED}

    def answer(payload):
        if payload.get("reference") in (None, ""):
            return _Resp(422, {"detail": "x"}, headers)
        return _Resp(200, {"ok": True})

    client = _ScriptedClient(answer)
    ns = _helpers(_emitted_routes(tmp_path), client)
    monkey = dict(_LumenLedger.CONSTRAINTS)
    _LumenLedger.CONSTRAINTS = {"phase": monkey["phase"], "reference": {}}
    try:
        resp, corr = ns["_post_accepting"](f"/v1/{CAP}", {"phase": "bloom"}, {}, CAP)
    finally:
        _LumenLedger.CONSTRAINTS = monkey
    assert resp.status_code == 200
    assert corr == ["reference=None->'R-1'"]


class _Resp:
    def __init__(self, status: int, body: Dict[str, Any], headers: Dict[str, str] | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        import json

        self.text = json.dumps(body)

    def json(self):
        return self._body


class _ScriptedClient:
    def __init__(self, answer):
        self.answer = answer
        self.posted: List[Dict[str, Any]] = []

    def post(self, path, json=None, headers=None):
        self.posted.append(dict(json or {}))
        return self.answer(dict(json or {}))


def test_json_keys_in_a_prose_only_rejection_never_reach_the_payload(tmp_path, product_models):
    """The exact live shape: no structured rejection, a body full of quoted
    keys. Nothing is scraped; the honest failure stands."""
    body = {"detail": "status must be one of: open, closed",
            "context": {"allowed_scopes": ["read"], "one of": "x"}}
    client = _ScriptedClient(lambda _p: _Resp(422, body))
    ns = _helpers(_emitted_routes(tmp_path), client)
    resp, corr = ns["_post_accepting"](
        f"/v1/{CAP}", {"reference": "sample", "status": "open"}, {}, CAP
    )
    assert resp.status_code == 422
    assert corr == []
    # One post, and no value anywhere in it came out of the response body.
    assert len(client.posted) == 1
    assert all("_scopes" not in str(v) for v in client.posted[0].values())
    assert client.posted[0]["reference"] == "sample"
    assert client.posted[0]["status"] == "open"


def test_the_handler_guard_body_is_read_the_same_way(tmp_path, product_models):
    """ok:false from the emitted handler guard carries the same facts."""
    guard_src = "def guard(payload):\n" + _constraint_guard(SPEC) + "\n    return None\n"
    ns_guard: Dict[str, Any] = {}
    exec(guard_src, ns_guard)
    refused = ns_guard["guard"]({"reference": "r", "phase": "wilt"})
    assert refused[REJECTED_FIELD_KEY] == "phase"
    assert refused[REJECTION_REASON_KEY] == NOT_ALLOWED
    assert refused[ALLOWED_VALUES_KEY] == ["sprout", "bloom"]

    def answer(payload):
        return _Resp(200, ns_guard["guard"](payload) or {"ok": True})

    client = _ScriptedClient(answer)
    ns = _helpers(_emitted_routes(tmp_path), client)
    resp, corr = ns["_post_accepting"](
        f"/v1/{CAP}", {"reference": "r", "phase": "wilt"}, {}, CAP
    )
    assert resp.json() == {"ok": True}
    assert corr == ["phase='wilt'->'sprout'"]


def test_a_route_that_keeps_refusing_the_value_it_named_stops_once(tmp_path, product_models):
    """No oscillation: each (field, value) is tried at most once."""
    headers = {
        REJECTED_FIELD_HEADER: '"phase"',
        REJECTION_REASON_HEADER: NOT_ALLOWED,
        ALLOWED_VALUES_HEADER: '["sprout"]',
    }
    client = _ScriptedClient(lambda _p: _Resp(422, {"detail": "no"}, headers))
    ns = _helpers(_emitted_routes(tmp_path), client)
    resp, corr = ns["_post_accepting"](
        f"/v1/{CAP}", {"reference": "r", "phase": "wilt"}, {}, CAP
    )
    assert resp.status_code == 422
    assert corr == ["phase='wilt'->'sprout'"]
    assert len(client.posted) == 2
