"""Declared placeholder connectors drive what every generated suite expects.

Live automotive build: a capability calling a connector the brief asked to
keep as an honest stub refused, correctly, and the generated suites expected
it to accept and round-trip its own record; TESTER stopped on
SAME_FAILURE_TWICE over an honest product. Every name below is invented: the
expectation must come from the blueprint's declaration, never from a name.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from app.factory.blueprint import ProductBlueprint
from app.factory.build.placeholder_connectors import (
    PRODUCT_MODULE,
    UNAVAILABLE_KIND,
    UNAVAILABLE_STATUS,
    brief_lines,
    placeholder_connectors,
    render_product_module,
    setting_for,
)

#: Invented: one capability on real blocks only, one calling an undeclared
#: system, one calling a declared placeholder connector.
WORKING = "lantern_ledger"
PLACEHOLDER = "tide_relay"
UNDECLARED = "kiln_board"
CONNECTOR = "moonwell_ledger_api"


def _blueprint() -> ProductBlueprint:
    return ProductBlueprint.model_validate(
        {
            "schema_version": "product_blueprint.v1",
            "product_id": "invented-product",
            "product_name": "Invented Product",
            "vertical": "product",
            "summary": "An invented product for the placeholder-connector contract.",
            "capabilities": [
                {"id": WORKING, "description": "keeps lantern entries"},
                {
                    "id": PLACEHOLDER,
                    "description": "relays tides to an outside ledger",
                    # Spelled the way a person types it: the slug is the key.
                    "connectors": ["Moonwell Ledger API"],
                },
                {
                    "id": UNDECLARED,
                    "description": "calls a system nobody declared a placeholder",
                    "connectors": ["glasshouse_feed"],
                },
            ],
            "connectors": [CONNECTOR],
        }
    )


def _module(tmp_path: Path):
    src = render_product_module(_blueprint())
    ns: dict = {}
    exec(compile(src, PRODUCT_MODULE, "exec"), ns)
    return SimpleNamespace(**ns)


def test_only_a_capability_calling_a_declared_placeholder_is_one():
    assert placeholder_connectors(_blueprint()) == {PLACEHOLDER: [CONNECTOR]}


def test_the_product_module_refuses_typed_and_names_the_setting(tmp_path):
    mod = _module(tmp_path)
    body = mod.refusal_for(PLACEHOLDER)
    assert body["ok"] is False
    assert body["error_kind"] == UNAVAILABLE_KIND == "unavailable"
    assert body["connectors"] == [CONNECTOR]
    assert body["settings"] == [setting_for(CONNECTOR)] == ["MOONWELL_LEDGER_API_CREDENTIALS"]
    assert setting_for(CONNECTOR) in body["error"]
    assert mod.refusal_for(WORKING) is None
    assert mod.refusal_for(UNDECLARED) is None
    assert mod.is_declared_refusal(PLACEHOLDER, UNAVAILABLE_STATUS, body)
    # The same body from a capability that declared nothing is not excused.
    assert not mod.is_declared_refusal(WORKING, UNAVAILABLE_STATUS, body)
    assert not mod.is_declared_refusal(PLACEHOLDER, 200, body)
    assert not mod.is_declared_refusal(PLACEHOLDER, UNAVAILABLE_STATUS, {"ok": False})


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)
        self.headers = {}

    def json(self):
        return self._body


def _run_route_suite(lines, post_answer):
    from app.factory.build import roles_handlers  # noqa: F401 -- import check

    src = "def _suite():\n    failures = []\n" + "\n".join(lines) + "\n    return failures\n"
    client = SimpleNamespace(
        post=lambda path, json=None, headers=None: post_answer,
        get=lambda path, headers=None: _Resp(200, {"items": []}),
    )
    from app.factory.build.payload_helpers import render_payload_helpers

    # The emitted suite defines the shared payload helpers at module level.
    ns = {"client": client, "AUTH": {}}
    exec("\n".join(render_payload_helpers()), ns)
    exec(src, ns)
    return ns["_suite"]()


def test_the_emitted_route_suite_expects_the_typed_refusal(tmp_path):
    from app.factory.build.roles_handlers import _declared_refusal_lines

    lines = _declared_refusal_lines(PLACEHOLDER, {"title": "sample"}, [CONNECTOR])
    typed = _module(tmp_path).refusal_for(PLACEHOLDER)
    assert _run_route_suite(lines, _Resp(UNAVAILABLE_STATUS, typed)) == []
    # Accepting the record is the WRONG answer for a declared placeholder.
    accepted = _run_route_suite(lines, _Resp(200, {"ok": True, "stored": {"id": 1}}))
    assert accepted and "want HTTP 503" in accepted[0]
    # A 503 that is not the typed refusal naming the setting fails too.
    assert _run_route_suite(lines, _Resp(UNAVAILABLE_STATUS, {"ok": False}))
    wrong = dict(typed, settings=["SOMETHING_ELSE"])
    assert _run_route_suite(lines, _Resp(UNAVAILABLE_STATUS, wrong))


def test_routes_refuse_after_auth_and_payload_checks_and_before_the_handler():
    from app.factory.build.roles_handlers import _render_routes

    src = _render_routes(
        [
            {
                "capability_id": PLACEHOLDER,
                "name": PLACEHOLDER,
                "entity": PLACEHOLDER,
                "body": "    return handle(payload)",
                "source": "test",
            }
        ]
    )
    assert "from app.placeholders import UNAVAILABLE_STATUS, refusal_for" in src
    auth = src.index("require_platform_token(request)")
    payload = src.index(f'reject_invalid_payload("{PLACEHOLDER}", payload)')
    guard = src.index("placeholder = refusal_for(CAPABILITY_ID)")
    handler = src.index(f"handle = _{PLACEHOLDER}_handle")
    assert auth < payload < guard < handler
    assert "JSONResponse(status_code=UNAVAILABLE_STATUS, content=placeholder)" in src
    compile(src, "routes.py", "exec")


def test_routes_ship_with_every_factory_module_they_import():
    """Structural: an emitted ``from app.<m> import`` naming a module the
    Factory stamps must arrive in the same unit as the routes. Rendering the
    routes alone once shipped ``import app.placeholders`` with no module."""
    import ast

    from app.factory.build.roles_handlers import render_routes_files
    from app.factory.build.store_acceptance import factory_rendered_paths

    files = render_routes_files(
        [{"capability_id": WORKING, "name": WORKING, "entity": WORKING,
          "body": "    return handle(payload)", "source": "test"}],
        _blueprint(),
    )
    shipped = {str(p).replace("\\", "/") for p in files}
    stamped = set(factory_rendered_paths())
    routes = files[Path("app") / "routes.py"]
    imported = {
        node.module.replace(".", "/") + ".py"
        for node in ast.walk(ast.parse(routes))
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.")
    }
    owed = imported & stamped
    assert PRODUCT_MODULE in owed  # the control: the check sees the import
    assert owed <= shipped, owed - shipped


def test_the_negative_floor_counts_the_typed_refusal_and_nothing_looser():
    from app.factory.build.negative_floor import render_negative_tests

    src = render_negative_tests({}, {})
    head = src.split("from app.main import app", 1)[1]
    head = head.split("def _required_field", 1)[0]
    ns: dict = {"os": SimpleNamespace(environ={"PLATFORM_TOKEN": "a", "PLATFORM_TOKEN_B": "b"})}
    exec(textwrap.dedent(head.replace("client = TestClient(app)", "")), ns)
    refused = ns["_refused"]
    typed = {"ok": False, "error_kind": UNAVAILABLE_KIND}
    assert refused(_Resp(UNAVAILABLE_STATUS, typed))
    assert refused(_Resp(422, {}))
    assert refused(_Resp(200, {"ok": False}))
    assert not refused(_Resp(UNAVAILABLE_STATUS, {"ok": False}))
    assert not refused(_Resp(500, typed))
    assert not refused(_Resp(200, {"ok": True}))


def test_the_writer_is_told_what_to_build():
    text = "\n".join(brief_lines(_blueprint()))
    assert PLACEHOLDER in text and setting_for(CONNECTOR) in text
    assert WORKING not in text and UNDECLARED not in text
    assert "503" in text and UNAVAILABLE_KIND in text
    assert brief_lines(ProductBlueprint.model_validate(
        {**_blueprint().model_dump(mode="json"), "connectors": []}
    )) == []


def test_the_behaviour_gate_reports_unjudged_records_without_failing(tmp_path):
    from app.factory.build.writer_behaviour import gate_writer_behaviour, probe_records

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("MODELS = {}\n", encoding="utf-8")
    record = {"gate_record": "unjudged", "kind": "placeholder",
              "text": f"{PLACEHOLDER}: not judgeable"}
    assert probe_records(json.dumps(record)) == [record]
    ctx = SimpleNamespace(
        workspace=tmp_path,
        run=lambda cmd: SimpleNamespace(returncode=0, stdout=json.dumps(record) + "\n", stderr=""),
    )
    result = gate_writer_behaviour(ctx)
    assert result.ok
    assert result.payload["unjudged"] == [record["text"]]
    assert result.payload["misses"] == []


# -- the round-trip probe against a booted (invented) product ---------------

_MODELS = '''
class _Rec:
    FIELDS = ["title"]
    CONSTRAINTS = {}
    __annotations__ = {"title": "str"}

MODELS = {%s}
'''

_STORE = '''
ROWS = {}

def save(entity, record):
    ROWS.setdefault(entity, []).append(dict(record, id=len(ROWS.get(entity, [])) + 1))
    return ROWS[entity][-1]

def list_all(entity, tenant_id=None):
    if entity not in ROWS:
        raise KeyError(entity)
    return list(ROWS[entity])
'''

_MAIN = '''
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app import store
from app.placeholders import UNAVAILABLE_STATUS, refusal_for

app = FastAPI()
for _cap in %r:
    store.ROWS.setdefault(_cap, [])

@app.post("/v1/{cap}")
def create(cap: str, payload: dict):
    refused = refusal_for(cap)
    if refused is not None:
        return JSONResponse(status_code=UNAVAILABLE_STATUS, content=refused)
    return {"ok": True, "stored": store.save(cap, payload)}

@app.get("/v1/{cap}")
def listing(cap: str):
    return {"items": store.list_all(cap)}
'''


def _invented_product(root: Path, caps, *, declaration: bool = True) -> None:
    app = root / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "models.py").write_text(
        _MODELS % ", ".join(f"{c!r}: _Rec" for c in caps), encoding="utf-8"
    )
    (app / "store.py").write_text(_STORE, encoding="utf-8")
    (app / "main.py").write_text(_MAIN % (list(caps),), encoding="utf-8")
    # The route declaration every Factory-rendered product carries: this
    # invented route saves each capability under its own id.
    (app / "routes.py").write_text(
        "ROUTE_ENTITIES = %r\n" % ({c: c for c in caps},), encoding="utf-8"
    )
    module = render_product_module(_blueprint()) if declaration else (
        "UNAVAILABLE_STATUS = 503\n"
        "def refusal_for(capability_id):\n    return None\n"
    )
    (root / PRODUCT_MODULE).write_text(module, encoding="utf-8")


def _probe(root: Path) -> str:
    from app.factory.build.product_gate import ROUND_TRIP_PROBE

    proc = subprocess.run(
        [sys.executable, "-c", ROUND_TRIP_PROBE],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.stdout + proc.stderr


def test_the_round_trip_probe_judges_working_capabilities_and_names_placeholders(tmp_path):
    _invented_product(tmp_path, [WORKING, PLACEHOLDER])
    out = _probe(tmp_path)
    assert "PRODUCT-SUMMARY: 1 round-tripped, 0 failed, 1 unjudged, 2 capabilities" in out, out
    assert f"GATE-UNJUDGED: {PLACEHOLDER} (declared placeholder connector(s) " in out
    assert setting_for(CONNECTOR) in out


def test_a_refusal_without_a_declaration_is_still_a_miss(tmp_path):
    # Same product refusing the same way, but the blueprint declared nothing
    # for this capability: the probe must not excuse it.
    _invented_product(tmp_path, [WORKING, PLACEHOLDER], declaration=False)
    (tmp_path / "app" / "main.py").write_text(
        (tmp_path / "app" / "main.py").read_text(encoding="utf-8").replace(
            "refused = refusal_for(cap)",
            "refused = {'ok': False, 'error_kind': 'unavailable'} if cap == %r else None"
            % PLACEHOLDER,
        ),
        encoding="utf-8",
    )
    out = _probe(tmp_path)
    assert f"GATE-MISS: {PLACEHOLDER}: POST answered HTTP 503" in out, out
    assert "1 round-tripped, 1 failed, 0 unjudged" in out


def test_the_architect_draft_declares_a_capability_s_placeholder_connectors():
    from app.factory.product_architect import _blueprint_from_llm_payload

    bp = _blueprint_from_llm_payload(
        {
            "product_name": "Invented Product",
            "summary": "invented",
            "capabilities": [
                {"id": WORKING, "description": "keeps entries", "block_ids": ["shelf_block"]},
                {
                    "id": PLACEHOLDER,
                    "description": "relays tides",
                    "block_ids": [],
                    # One system a block supplies, one nothing supplies.
                    "connectors": ["shelf_block", "Moonwell Ledger API"],
                },
            ],
        },
        "an invented brief",
        None,
        ["shelf_block"],
    )
    assert bp.connectors == [CONNECTOR]
    assert placeholder_connectors(bp) == {PLACEHOLDER: [CONNECTOR]}
