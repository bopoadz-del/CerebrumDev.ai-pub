"""The round-trip reads where a capability DECLARES it persists, under the
tenant its route wrote with -- never an entity guessed from the capability id.

Live 2026-10-06 (879ed1e1, co-op repro): TESTER stopped with
FAILED(TESTER, round_trip, ...) after every capability read as "no readable
entity". The probe called the canonical store's ``list_all(entity)`` with no
``tenant_id`` and fell back to entity == capability id, so it judged nothing.
These tests boot an invented product whose store requires a tenant and whose
routes save under entities named differently from their capabilities.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.factory.build import product_gate, writer_behaviour
from app.factory.build.roles_handlers import _render_routes

#: Invented capability -> the entity its route saves to (deliberately not
#: the capability id).
SAVES = {"kiln_ledger": "firing_log", "loom_roster": "weaver"}
READ_ONLY = "quarry_overview"

_MODELS = '''
class _Rec:
    FIELDS = ["title"]
    CONSTRAINTS = {}
    __annotations__ = {"title": "str"}

MODELS = {%s}
'''

_STORE = '''
ROWS = {}

def save(entity, record, tenant_id):
    rows = ROWS.setdefault((entity, tenant_id), [])
    rows.append(dict(record, id=len(rows) + 1))
    return rows[-1]

def list_all(entity, tenant_id):
    key = (entity, tenant_id)
    if key not in ROWS:
        raise KeyError(entity)
    return list(ROWS[key])
'''

_TENANCY = '''
class _Tenant:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id

def resolve_tenant(headers):
    header = str(headers.get("Authorization") or "")
    if header != "Bearer dev-local-token":
        raise ValueError("unknown token")
    return _Tenant("t-alpha")
'''

_MAIN = '''
from fastapi import FastAPI, Request
from app import store
from app.routes import ROUTE_ENTITIES
from app.tenancy import resolve_tenant

app = FastAPI()
SAVE = %r
# Every declared entity has a (migrated, empty) table, like a SQL store.
for _entity in set(SAVE.values()) | set(ROUTE_ENTITIES.values()):
    store.ROWS.setdefault((_entity, "t-alpha"), [])

@app.post("/v1/{cap}")
def create(cap: str, payload: dict, request: Request):
    tenant = resolve_tenant(request.headers)
    entity = SAVE.get(cap)
    if entity:
        store.save(entity, payload, tenant_id=tenant.tenant_id)
    return {"ok": True}

@app.get("/v1/{cap}")
def listing(cap: str, request: Request):
    tenant = resolve_tenant(request.headers)
    entity = ROUTE_ENTITIES.get(cap)
    return {"items": store.list_all(entity, tenant_id=tenant.tenant_id) if entity else []}
'''


def _product(root: Path, caps, *, route_entities, saves, actions=None) -> None:
    app = root / "app"
    (app / "actions").mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "actions" / "__init__.py").write_text("", encoding="utf-8")
    (app / "models.py").write_text(
        _MODELS % ", ".join(f"{c!r}: _Rec" for c in caps), encoding="utf-8"
    )
    (app / "store.py").write_text(_STORE, encoding="utf-8")
    (app / "tenancy.py").write_text(_TENANCY, encoding="utf-8")
    (app / "routes.py").write_text(
        "ROUTE_ENTITIES = %r\n" % (route_entities,), encoding="utf-8"
    )
    (app / "main.py").write_text(_MAIN % (saves,), encoding="utf-8")
    for cap, text in (actions or {}).items():
        (app / "actions" / f"{cap}.py").write_text(text, encoding="utf-8")


def _probe(root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", product_gate.ROUND_TRIP_PROBE],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    return proc.stdout + proc.stderr


def test_entities_named_unlike_their_capabilities_round_trip(tmp_path):
    _product(tmp_path, list(SAVES), route_entities=SAVES, saves=SAVES)
    out = _probe(tmp_path)
    assert "PRODUCT-SUMMARY: 2 round-tripped, 0 failed, 0 unjudged, 2 capabilities" in out, out
    assert "no readable entity" not in out


def test_a_declared_read_only_capability_is_unjudged_not_failed(tmp_path):
    caps = [*SAVES, READ_ONLY]
    _product(
        tmp_path, caps, route_entities=SAVES, saves=SAVES,
        actions={READ_ONLY: "ENTITY = None\n"},
    )
    out = _probe(tmp_path)
    assert f"GATE-UNJUDGED: {READ_ONLY} (declares no persisted entity)" in out, out
    assert "PRODUCT-SUMMARY: 2 round-tripped, 0 failed, 1 unjudged, 3 capabilities" in out, out


def test_a_declared_entity_that_is_never_written_still_fails(tmp_path):
    cap = next(iter(SAVES))
    _product(tmp_path, [cap], route_entities={cap: SAVES[cap]}, saves={})
    out = _probe(tmp_path)
    assert f"GATE-MISS: {cap}: POST reported success and {SAVES[cap]} holds 0 row(s)" in out, out
    assert "0 round-tripped, 1 failed" in out


def test_an_undeclared_capability_is_named_never_guessed(tmp_path):
    cap = next(iter(SAVES))
    # The route saves under the capability id itself, but nothing declares
    # it: the probe must not assume it.
    _product(tmp_path, [cap], route_entities={}, saves={cap: cap})
    out = _probe(tmp_path)
    assert f"GATE-MISS: {cap}: declares no store entity" in out, out


def test_the_rendered_routes_declare_the_entity_each_route_saves_to():
    entries = [
        {"capability_id": cap, "name": cap, "entity": entity, "body": "    return {}",
         "source": "template"}
        for cap, entity in SAVES.items()
    ]
    source = _render_routes(entries)
    namespace: dict = {}
    line = next(ln for ln in source.splitlines() if ln.startswith("ROUTE_ENTITIES = "))
    exec(line, namespace)  # noqa: S102 -- the rendered literal, nothing else
    assert namespace["ROUTE_ENTITIES"] == SAVES
    for cap, entity in SAVES.items():
        assert f'store.save("{entity}", record' in source


def test_no_probe_falls_back_to_the_capability_id_or_a_fixed_tenant():
    for probe in (product_gate.ROUND_TRIP_PROBE, writer_behaviour._render_probe()):
        assert "_declared_entity" in probe
        assert 'getattr(cls, "ENTITY", None) or cap_id' not in probe
        assert 'tenant_id="local"' not in probe
