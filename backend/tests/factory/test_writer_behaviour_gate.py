"""The WRITER gate rejects a route that reports success over a failed block.

Failing-first by construction: the same workspace is probed twice, differing
only in whether the route checks its handler's result before persisting. A
gate that passed both would not be testing anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build.gates import GateContext, gate_writer_contract

CAPABILITIES_ROUTE = """
@app.get("/v1/capabilities")
def capabilities():
    from pathlib import Path
    here = Path(__file__).parent / "actions"
    ids = sorted(p.stem for p in here.glob("*.py") if p.stem != "__init__")
    return {"items": ids}
"""

#: Discovers capabilities and builds every route from them, as the
#: factory's own default console does.
CONSOLE = (
    "<!doctype html><html><body><script>"
    "fetch('/v1/capabilities');"
    'const url = "/v1/" + cap;'
    "</script></body></html>"
)
from app.factory.build.authority import BuildRole
from app.factory.build.writer_behaviour import (
    BEHAVIOUR_PROBE,
    CONTRACT_HALT,
    F1_HALT,
    F11_HALT,
    GATE_NAME,
    SCHEMA_HALT,
    SCHEMA_SQL_HALT,
    HALT_SENTENCES,
    _render_probe,
    banner_from_records,
    classify_unmarked_probe_failure,
    findings_from_probe_stderr,
    probe_records,
)


def _r(level, kind, text):
    """A typed probe record, exactly as the probe writes one."""
    return {"gate_record": level, "kind": kind, "text": text}

# A route body that persists whatever arrived, regardless of the handler.
# This is LotDesk's shape (app/routes.py:127-128 of the shipped artifact).
_UNGUARDED = """    result = handle(payload)
    saved = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}
"""

# The same route, checking the handler before it writes.
_GUARDED = """    result = handle(payload)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    saved = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}
"""


def _write_workspace(root: Path, route_body: str) -> None:
    app = root / "app"
    (app / "actions").mkdir(parents=True, exist_ok=True)

    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import widget_intake  # noqa: F401\n", encoding="utf-8"
    )

    (app / "dispatch.py").write_text(
        'def execute(block_id, payload=None, action=None, params=None):\n'
        '    return {"status": "ok", "block": block_id}\n',
        encoding="utf-8",
    )

    (app / "models.py").write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from typing import Optional\n\n"
        "@dataclass\n"
        "class Widget:\n"
        "    id: Optional[int] = None\n"
        "    name: str = ''\n"
        "    size: int = 1\n"
        "    status: str = 'new'\n"
        "    FIELDS = ['name', 'size', 'status']\n"
        "    CONSTRAINTS = {'size': {'min': 1, 'max': 99},\n"
        "                   'status': {'allowed_values': ['new', 'done']}}\n\n"
        "MODELS = {'widget_intake': Widget}\n",
        encoding="utf-8",
    )

    (app / "jobs.py").write_text(
        "CAPABILITIES = [{'id': 'widget_intake', 'entity': 'widget'}]\n"
        "JOBS = []\nCATALOG = {}\nGATES = {}\n",
        encoding="utf-8",
    )

    # Entity name deliberately differs from the capability id, so a gate that
    # guesses the entity finds nothing and its row assertion goes inert.
    (app / "store.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n\n"
        "def _f(entity):\n"
        "    root = Path(os.getenv('STORAGE_PATH', './data'))\n"
        "    root.mkdir(parents=True, exist_ok=True)\n"
        "    return root / (entity + '.json')\n\n"
        "def list_all(entity, tenant_id=None):\n"
        "    p = _f(entity)\n"
        "    return json.loads(p.read_text()) if p.is_file() else []\n\n"
        "def save(entity, record, tenant_id=None):\n"
        "    rows = list_all(entity)\n"
        "    rows.append(record)\n"
        "    _f(entity).write_text(json.dumps(rows))\n"
        "    return record\n",
        encoding="utf-8",
    )

    (app / "actions" / "widget_intake.py").write_text(
        '"""Handler for capability widget_intake.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n'
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'widget_intake'\n\n"
        "def handle(payload):\n"
        "    res = execute('database', {'input': payload}, action='insert')\n"
        "    if res.get('status') == 'error':\n"
        "        return {'ok': False, 'error': res.get('error')}\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n",
        encoding="utf-8",
    )

    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        "from app.actions import widget_intake as _action\n\n"
        "router = APIRouter()\n\n"
        "def _handle(payload):\n"
        "    return _action.handle(payload)\n\n"
        '@router.post("/widget_intake")\n'
        "def widget_intake_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        '    CAPABILITY_ID = "widget_intake"\n'
        "    handle = _handle\n"
        '    save = lambda record: store.save("widget", record)\n'
        f"{route_body}",
        encoding="utf-8",
    )

    (app / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from app.routes import router\n"
        'app = FastAPI(title="probe")\n'
        'app.include_router(router, prefix="/v1")\n\n'
        '@app.get("/health")\n'
        "def health():\n"
        '    return {"status": "ok"}\n',
        encoding="utf-8",
    )

    # A product lists its capabilities and serves a console that drives
    # them: the writer contract now includes ui_end_to_end.
    with (app / "main.py").open("a", encoding="utf-8") as handle:
        handle.write(CAPABILITIES_ROUTE)
    static = app / "static"
    static.mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text(CONSOLE, encoding="utf-8")


def _run_gate(workspace: Path):
    def runner(argv, *, cwd, timeout):
        return subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )

    ctx = GateContext(workspace=workspace, role=BuildRole.WRITER, runner=runner)
    return gate_writer_contract(ctx)


@pytest.mark.skipif(
    sys.version_info < (3, 9), reason="probe uses modern typing in the fixture"
)
def test_gate_rejects_route_that_persists_over_a_failed_block(tmp_path):
    _write_workspace(tmp_path, _UNGUARDED)
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.gate == GATE_NAME
    assert result.detail == F1_HALT, result.detail
    joined = " ".join(result.findings)
    assert "did not fail closed" in joined, joined
    # The row assertion must actually fire, not go quiet because the entity
    # name was guessed from the capability id.
    assert "persisted" in joined, joined


def test_gate_passes_when_the_route_checks_its_handler(tmp_path):
    _write_workspace(tmp_path, _GUARDED)
    result = _run_gate(tmp_path)

    assert result.ok is True, result.findings


def test_gate_reports_a_writer_that_produced_nothing(tmp_path):
    """0.5: zero agent-authored artifacts is a hard refusal, not a miss."""
    result = _run_gate(tmp_path)

    assert result.ok is False
    assert "writer_no_output" in result.detail
    assert "writer_no_output" in result.findings


# A coder-shaped handler that ignores execute() errors — the live
# invoice-management shape (moonshot/kimi, 2026-08-30).
_DISHONEST_HANDLER = """    res = execute('database', {'input': payload}, action='insert')
    return {'ok': True, 'capability': CAPABILITY_ID, 'ignored': res}
"""


def _write_mixed_workspace(root: Path) -> None:
    """One honest capability and one that reports success over a failed block.

    The live Floor run had six capabilities; writer_behaviour failed the
    whole WRITER phase because one route lied. Isolated F1 is a miss.
    """
    _write_workspace(root, _GUARDED)

    app = root / "app"
    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            "MODELS = {'widget_intake': Widget, 'invoice_management': Widget}\n",
        ),
        encoding="utf-8",
    )
    jobs = (app / "jobs.py").read_text(encoding="utf-8")
    (app / "jobs.py").write_text(
        jobs.replace(
            "CAPABILITIES = [{'id': 'widget_intake', 'entity': 'widget'}]\n",
            "CAPABILITIES = ["
            "{'id': 'widget_intake', 'entity': 'widget'}, "
            "{'id': 'invoice_management', 'entity': 'invoice'}]\n",
        ),
        encoding="utf-8",
    )
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import widget_intake  # noqa: F401\n"
        "from app.actions import invoice_management  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "actions" / "invoice_management.py").write_text(
        '"""Handler for capability invoice_management.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n'
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'invoice_management'\n\n"
        "def handle(payload):\n"
        + _DISHONEST_HANDLER,
        encoding="utf-8",
    )
    routes = (app / "routes.py").read_text(encoding="utf-8")
    extra = '''
from app.actions import invoice_management as _invoice_action

@router.post("/invoice_management")
def invoice_management_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    CAPABILITY_ID = "invoice_management"
    handle = _invoice_action.handle
    save = lambda record: store.save("invoice", record)
    result = handle(payload)
    saved = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}
'''
    (app / "routes.py").write_text(routes + extra, encoding="utf-8")


def test_gate_records_an_isolated_success_over_failed_block_as_a_miss(tmp_path):
    """Regression for the live halt: one F1 must not fail the WRITER gate.

    A workspace with one honest capability and one that reports success
    over a failed block used to exit writer_behaviour non-zero and stop
    the run before TESTER/STORE_MANAGER (no zip). Isolated F1 is a miss.
    """
    _write_mixed_workspace(tmp_path)
    result = _run_gate(tmp_path)

    assert result.ok is True, result
    joined = " ".join(result.findings) + " " + " ".join(result.payload.get("misses") or [])
    assert "invoice_management" in joined, (result.detail, joined)
    assert "did not fail closed" in joined or "persisted" in joined, joined
    assert "miss" in result.detail
    assert "widget_intake" not in joined


def test_kernel_route_does_not_report_success_over_a_failed_block(tmp_path):
    """Live path: templated route -> run_capability -> dishonest handle().

    Isolated subprocess so the workspace's ``app`` package does not collide
    with the factory host. The coder handler returns ok:True after
    execute() errors; the kernel bridge must refuse ActionStatus.SUCCESS
    so the route cannot persist or answer ok:True.
    """
    import shutil

    from app.factory.build.roles import _render_kernel_bridge, _templated_route_body

    app = tmp_path / "app"
    (app / "actions").mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import invoice_management  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        "    return {'status': 'error', 'block': block_id,\n"
        "            'error': 'writer_behaviour gate: forced block failure'}\n",
        encoding="utf-8",
    )
    (app / "actions" / "invoice_management.py").write_text(
        '"""Handler for capability invoice_management.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n'
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'invoice_management'\n\n"
        "def handle(payload):\n"
        + _DISHONEST_HANDLER,
        encoding="utf-8",
    )
    (app / "tenancy.py").write_text(
        "class TenantRefused(Exception):\n"
        "    pass\n\n"
        "class _Tenant:\n"
        "    def __init__(self, tenant_id):\n"
        "        self.tenant_id = tenant_id\n"
        "        self.roles = []\n\n"
        "TENANT_TOKENS = {'dev-local-token': 'local'}\n\n"
        "def resolve_tenant(headers):\n"
        "    auth = (headers or {}).get('authorization', '')\n"
        "    token = auth[7:].strip() if auth.startswith('Bearer ') else ''\n"
        "    tid = TENANT_TOKENS.get(token)\n"
        "    if tid is None:\n"
        "        raise TenantRefused(token)\n"
        "    return _Tenant(tid)\n",
        encoding="utf-8",
    )
    (app / "kernel_bridge.py").write_text(_render_kernel_bridge(), encoding="utf-8")
    kernel_src = Path(__file__).resolve().parents[2] / "app" / "cerebrum_product_kernel"
    shutil.copytree(kernel_src, app / "cerebrum_product_kernel")

    route_body = _templated_route_body({"fields": []})
    (tmp_path / "probe.py").write_text(
        "import asyncio\n"
        "from app.kernel_bridge import run_capability\n"
        "\n"
        "saved = []\n"
        "\n"
        "async def _route(payload, run_capability, save, request, CAPABILITY_ID='invoice_management'):\n"
        f"{route_body}\n"
        "\n"
        "class _Req:\n"
        "    headers = {'authorization': 'Bearer dev-local-token'}\n"
        "\n"
        "out = asyncio.run(_route({'reference': 'INV-1'}, run_capability, saved.append, _Req()))\n"
        "print(out)\n"
        "print('SAVED', saved)\n"
        "assert out.get('ok') is False, out\n"
        "assert saved == [], out\n"
        "assert out.get('result', {}).get('status') != 'success', out\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "probe.py"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHONPATH": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_probe_and_the_host_share_one_set_of_halt_sentences():
    """Each halt kind's sentence is the host constant, rendered INTO the probe:
    the probe emits ``_halt(kind)`` and never spells the sentence itself."""
    rendered = _render_probe()
    for kind, sentence in (
        ("schema", SCHEMA_HALT), ("f11", F11_HALT),
        ("contract", CONTRACT_HALT), ("migration", SCHEMA_SQL_HALT),
    ):
        assert HALT_SENTENCES[kind] == sentence
        assert sentence in rendered
        assert '_halt("%s"' % kind in BEHAVIOUR_PROBE


def test_the_probe_writes_typed_records_and_no_marker_lines():
    assert "GATE-FINDING" not in BEHAVIOUR_PROBE
    assert "GATE-MISS" not in BEHAVIOUR_PROBE
    line = '{"gate_record": "miss", "kind": "f11", "text": "x: declares block(s) (F11)"}'
    assert probe_records("library noise\n" + line) == [_r("miss", "f11", "x: declares block(s) (F11)")]


def test_banner_contract_does_not_masquerade_as_f1():
    """Live makerspace Floor banner must name the contract halt, not F1."""
    line = (
        "dashboards_and_reports: dashboard: the action travelled inside "
        "the payload; Answered 'Unknown action: None' (CONTRACT: unknown action)"
    )
    recs = [_r("halt", "contract", CONTRACT_HALT), _r("finding", "contract", line)]
    assert banner_from_records(recs) == CONTRACT_HALT
    assert F1_HALT not in banner_from_records(recs)


def test_banner_schema_does_not_masquerade_as_f1():
    """Live construction: API findings were schema, Floor banner said F1."""
    assert banner_from_records([_r("halt", "schema", SCHEMA_HALT)]) == SCHEMA_HALT
    assert banner_from_records([
        _r("halt", "schema", SCHEMA_HALT),
        _r("finding", "schema", "site_diary_capture: refused a payload (x)"),
    ]) == SCHEMA_HALT
    assert banner_from_records([
        _r("finding", "f1", "widget_intake: did not fail closed (F1)")
    ]) == F1_HALT
    assert banner_from_records([]) == "behaviour probe failed with no output"
    assert banner_from_records([
        _r("halt", "import", HALT_SENTENCES["import"]),
        _r("finding", "import", "ModuleNotFoundError: x"),
    ]) == HALT_SENTENCES["import"] + ": ModuleNotFoundError: x"


def test_banner_f11_does_not_masquerade_as_f1():
    """Live construction: API findings were F11, Floor banner said F1. The
    kind decides -- the text may contain any substring it likes."""
    f11_line = "daily_site_diary: declares block(s) it never invokes: workflow (F11)"
    recs = [_r("halt", "f11", F11_HALT), _r("finding", "f11", f11_line)]
    assert banner_from_records(recs) == F11_HALT
    assert F1_HALT not in banner_from_records(recs)


def test_a_records_kind_decides_never_its_words():
    """A finding whose TEXT carries another class's marker is still its own
    kind: the host reads the record, not the sentence."""
    misleading = "x: did not fail closed -- persisted (F1) (F11) (CONTRACT"
    assert banner_from_records([_r("halt", "schema", SCHEMA_HALT),
                                _r("finding", "schema", misleading)]) == SCHEMA_HALT


def _add_schema_refuser(root: Path, cap_id: str = "broken_schema") -> None:
    """A capability that refuses its own payload and never calls a block."""
    app = root / "app"
    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            f"MODELS = {{'widget_intake': Widget, '{cap_id}': Widget}}\n",
        )
        if "MODELS = {'widget_intake': Widget}\n" in models
        else models.replace(
            "MODELS = {'widget_intake': Widget, 'invoice_management': Widget}\n",
            "MODELS = {'widget_intake': Widget, "
            f"'invoice_management': Widget, '{cap_id}': Widget}}\n",
        ),
        encoding="utf-8",
    )
    jobs = (app / "jobs.py").read_text(encoding="utf-8")
    (app / "jobs.py").write_text(
        jobs.replace(
            "{'id': 'widget_intake', 'entity': 'widget'}",
            "{'id': 'widget_intake', 'entity': 'widget'}, "
            f"{{'id': '{cap_id}', 'entity': '{cap_id}'}}",
        ),
        encoding="utf-8",
    )
    init = (app / "actions" / "__init__.py").read_text(encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        init + f"from app.actions import {cap_id}  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "actions" / f"{cap_id}.py").write_text(
        f'"""Handler for capability {cap_id}.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n'
        f"CAPABILITY_ID = '{cap_id}'\n\n"
        "def handle(payload):\n"
        "    return {'ok': False, 'error': 'name is required'}\n",
        encoding="utf-8",
    )
    routes = (app / "routes.py").read_text(encoding="utf-8")
    extra = f'''
from app.actions import {cap_id} as _{cap_id}_action

@router.post("/{cap_id}")
def {cap_id}_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _{cap_id}_action.handle(payload)
'''
    (app / "routes.py").write_text(routes + extra, encoding="utf-8")


def test_gate_records_isolated_schema_refusal_as_a_miss(tmp_path):
    """Regression: schema miss must not halt a mixed workspace or look like F1.

    Live construction (sess_c6aa774686894c5a): findings were
    ``no capability accepted its own schema`` but the Floor banner said
    success-over-failed-block and WRITER SystemExit'd before TESTER.
    Isolated schema refusal next to an honest capability is a miss.
    """
    _write_workspace(tmp_path, _GUARDED)
    _add_schema_refuser(tmp_path)
    result = _run_gate(tmp_path)

    assert result.ok is True, result
    joined = " ".join(result.findings) + " " + result.detail
    assert "broken_schema" in joined, (result.detail, result.findings)
    assert "refused a payload" in joined or "schema" in result.detail, joined
    assert result.detail != F1_HALT
    assert F1_HALT not in result.detail
    assert "success over a failed block" not in result.detail
    assert SCHEMA_HALT not in result.findings
    payload = result.payload or {}
    assert any("broken_schema" in m for m in (payload.get("schema_misses") or payload.get("misses") or []))


def test_gate_fails_all_schema_with_schema_detail_not_f1(tmp_path):
    """Every capability refused its own schema: fail, but say schema not F1."""
    _write_workspace(tmp_path, _GUARDED)
    # Replace the honest route with a schema refuser so the workspace
    # has only capabilities that never accept their own payload.
    app = tmp_path / "app"
    (app / "actions" / "widget_intake.py").write_text(
        '"""Handler for capability widget_intake.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n'
        "CAPABILITY_ID = 'widget_intake'\n\n"
        "def handle(payload):\n"
        "    return {'ok': False, 'error': 'name is required'}\n",
        encoding="utf-8",
    )
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app.actions import widget_intake as _action\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/widget_intake")\n'
        "def widget_intake_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        "    return _action.handle(payload)\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.detail == SCHEMA_HALT, result.detail
    assert SCHEMA_HALT in result.findings
    assert result.detail != F1_HALT
    assert "success over a failed block" not in result.detail


def test_gate_does_not_halt_when_baseline_ok_false_because_block_failed(tmp_path):
    """Kernel wrap (#237): real blocks fail on the sample payload → ok:False.

    That used to empty ``targets`` and SystemExit with the schema sentence,
    which the host then labelled F1. A block was reached, so phase two
    must still run. An honest fail-closed handler passes.
    """
    _write_workspace(tmp_path, _GUARDED)
    app = tmp_path / "app"
    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        "    return {'status': 'error', 'block': block_id, 'error_kind': 'unavailable',\n"
        "            'error': 'sample payload rejected by block'}\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)

    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != F1_HALT
    assert SCHEMA_HALT not in (result.findings or [])
    assert result.detail != SCHEMA_HALT


def test_kernel_templated_route_with_failing_blocks_does_not_halt_as_schema(tmp_path):
    """Live path: kernel execute_action template + blocks that reject sample.

    last_event on the construction run was ``wrote route … (kernel
    execute_action template)``. After #237 the wrap answers ok:False when
    a block fails, which the schema phase treated as 'did not accept' and
    halted the whole WRITER. The miss parser never saw phase two.
    """
    import shutil

    from app.factory.build.roles import _render_kernel_bridge, _templated_route_body

    _write_workspace(tmp_path, _GUARDED)
    app = tmp_path / "app"
    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        "    return {'status': 'error', 'block': block_id, 'error_kind': 'unavailable',\n"
        "            'error': 'sample payload rejected by block'}\n",
        encoding="utf-8",
    )
    (app / "tenancy.py").write_text(
        "class TenantRefused(Exception):\n"
        "    pass\n\n"
        "class _Tenant:\n"
        "    def __init__(self, tenant_id):\n"
        "        self.tenant_id = tenant_id\n"
        "        self.roles = []\n\n"
        "TENANT_TOKENS = {'dev-local-token': 'local'}\n\n"
        "def resolve_tenant(headers):\n"
        "    auth = (headers or {}).get('authorization', '')\n"
        "    token = auth[7:].strip() if auth.startswith('Bearer ') else ''\n"
        "    tid = TENANT_TOKENS.get(token)\n"
        "    if tid is None:\n"
        "        raise TenantRefused(token)\n"
        "    return _Tenant(tid)\n",
        encoding="utf-8",
    )
    (app / "kernel_bridge.py").write_text(_render_kernel_bridge(), encoding="utf-8")
    kernel_src = Path(__file__).resolve().parents[2] / "app" / "cerebrum_product_kernel"
    shutil.copytree(kernel_src, app / "cerebrum_product_kernel")

    route_body = _templated_route_body({"fields": []})
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter, Request\n"
        "from app import store\n"
        "from app.kernel_bridge import run_capability\n"
        "from app.tenancy import resolve_tenant\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/widget_intake")\n'
        "async def widget_intake_create(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:\n"
        '    CAPABILITY_ID = "widget_intake"\n'
        "    tenant = resolve_tenant(request.headers)\n"
        '    save = lambda record: store.save("widget", record, tenant_id=tenant.tenant_id)\n'
        f"{route_body}\n",
        encoding="utf-8",
    )
    result = _run_gate(tmp_path)

    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != F1_HALT
    assert result.detail != SCHEMA_HALT
    assert SCHEMA_HALT not in (result.findings or [])


def _declare_blocks(root: Path, cap_id: str, block_ids: list[str]) -> None:
    """Set BLOCK_IDS on an existing action module without changing handle()."""
    path = root / "app" / "actions" / f"{cap_id}.py"
    text = path.read_text(encoding="utf-8")
    needle = f"CAPABILITY_ID = '{cap_id}'\n"
    assert needle in text, text
    if "BLOCK_IDS" in text:
        return
    path.write_text(
        text.replace(needle, needle + f"BLOCK_IDS = {block_ids!r}\n"),
        encoding="utf-8",
    )


def _add_f11_unused(root: Path, cap_id: str, unused: list[str]) -> None:
    """A fail-closed capability that declares unused BLOCK_IDS (F11).

    Invokes ``database`` and refuses success when that block fails, so it
    is F1-honest. Extra declared ids are never passed to execute().
    """
    app = root / "app"
    models = (app / "models.py").read_text(encoding="utf-8")
    if "MODELS = {'widget_intake': Widget}\n" in models:
        models = models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            f"MODELS = {{'widget_intake': Widget, '{cap_id}': Widget}}\n",
        )
    else:
        models = models.replace(
            "}\n",
            f", '{cap_id}': Widget}}\n",
            1,
        )
    (app / "models.py").write_text(models, encoding="utf-8")
    jobs = (app / "jobs.py").read_text(encoding="utf-8")
    (app / "jobs.py").write_text(
        jobs.replace(
            "{'id': 'widget_intake', 'entity': 'widget'}",
            "{'id': 'widget_intake', 'entity': 'widget'}, "
            f"{{'id': '{cap_id}', 'entity': '{cap_id}'}}",
        ),
        encoding="utf-8",
    )
    init = (app / "actions" / "__init__.py").read_text(encoding="utf-8")
    (app / "actions" / "__init__.py").write_text(
        init + f"from app.actions import {cap_id}  # noqa: F401\n",
        encoding="utf-8",
    )
    declared = ["database"] + list(unused)
    (app / "actions" / f"{cap_id}.py").write_text(
        "from app.dispatch import execute\n\n"
        f"CAPABILITY_ID = '{cap_id}'\n"
        f"BLOCK_IDS = {declared!r}\n\n"
        "def handle(payload):\n"
        "    res = execute('database', {'input': payload}, action='insert')\n"
        "    if res.get('status') == 'error':\n"
        "        return {'ok': False, 'error': res.get('error')}\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n",
        encoding="utf-8",
    )
    routes = (app / "routes.py").read_text(encoding="utf-8")
    extra = f'''
from app.actions import {cap_id} as _{cap_id}_action

@router.post("/{cap_id}")
def {cap_id}_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    CAPABILITY_ID = "{cap_id}"
    handle = _{cap_id}_action.handle
    save = lambda record: store.save("{cap_id}", record)
    result = handle(payload)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    saved = save(payload)
    return {{"ok": True, "capability": CAPABILITY_ID, "stored": saved}}
'''
    (app / "routes.py").write_text(routes + extra, encoding="utf-8")


def test_gate_records_isolated_f11_unused_blocks_as_a_miss(tmp_path):
    """Regression: isolated F11 must not halt a mixed workspace or look like F1.

    Live construction (sess_526eed111e41468a): WRITER wrote 5/5 routes,
    then writer_behaviour SystemExit'd on
    ``daily_site_diary: declares block(s) it never invokes: workflow (F11)``
    before TESTER/STORE_MANAGER (no zip). Isolated F11 is a miss.
    """
    _write_workspace(tmp_path, _GUARDED)
    _declare_blocks(tmp_path, "widget_intake", ["database"])
    _add_f11_unused(tmp_path, "daily_site_diary", unused=["workflow"])
    result = _run_gate(tmp_path)

    assert result.ok is True, result
    joined = " ".join(result.findings) + " " + result.detail
    assert "daily_site_diary" in joined, (result.detail, result.findings)
    assert "(F11)" in joined or "never invoke" in joined, joined
    assert result.detail != F1_HALT
    assert result.detail != F11_HALT
    assert result.detail != SCHEMA_HALT
    assert F1_HALT not in result.detail
    assert "success over a failed block" not in result.detail
    payload = result.payload or {}
    f11 = payload.get("f11_misses") or []
    assert any("daily_site_diary" in m and "(F11)" in m for m in f11), payload
    assert not any("widget_intake" in m for m in f11)
    assert not (payload.get("f1_misses") or [])


def test_gate_fails_all_f11_with_f11_detail_not_f1(tmp_path):
    """Every capability declared unused blocks: fail, but say F11 not F1."""
    _write_workspace(tmp_path, _GUARDED)
    _declare_blocks(tmp_path, "widget_intake", ["database", "workflow"])
    _add_f11_unused(tmp_path, "punch_list_tracking", unused=["team", "workflow"])
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.detail == F11_HALT, result.detail
    assert F11_HALT in result.findings
    assert result.detail != F1_HALT
    assert F1_HALT not in result.detail
    assert "success over a failed block" not in result.detail
    joined = " ".join(result.findings)
    assert "widget_intake" in joined
    assert "punch_list_tracking" in joined
    assert "(F11)" in joined


# -- live veterinary-care / appointment-shaped workspace (2026-09-03) ------

_LIVE_SQL_STDERR = """
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_baseline
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unknown column "id" in primary key
[SQL:
CREATE TABLE appointment (
id INTEGER NOT NULL,
scheduled_time TEXT,
duration_minutes INTEGER,
status TEXT,
service_type TEXT,
PRIMARY KEY (id)
)

]
(Background on this error at: https://sqlalche.me/e/20/e3q8)
"""

_INVALID_APPOINTMENT_REVISION = '''"""v1 domain tables — live-broken PRIMARY KEY without id column."""
from __future__ import annotations

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE appointment (
            scheduled_time TEXT,
            duration_minutes INTEGER,
            status TEXT,
            service_type TEXT,
            PRIMARY KEY (id)
        )
        """
    )


def downgrade() -> None:
    op.drop_table("appointment")
'''


def test_unmarked_sql_stderr_is_not_the_floor_banner():
    """Live Floor banner was ``scheduled_time TEXT,`` from raw[-8:]. Unmarked
    stderr is classified by the traceback's exception CLASS."""
    findings = findings_from_probe_stderr(_LIVE_SQL_STDERR)
    assert findings
    joined = " ".join(findings)
    assert "scheduled_time TEXT" not in joined
    assert SCHEMA_SQL_HALT in joined
    detail = banner_from_records(probe_records(_LIVE_SQL_STDERR), _LIVE_SQL_STDERR)
    assert "scheduled_time TEXT" not in detail
    assert SCHEMA_SQL_HALT in detail
    assert F1_HALT not in detail
    assert SCHEMA_HALT not in detail


def test_a_raw_line_is_never_the_banner():
    sql_lines = [
        "scheduled_time TEXT,",
        "duration_minutes INTEGER,",
        "status TEXT,",
        "service_type TEXT,",
        "PRIMARY KEY (id)",
    ]
    banner = banner_from_records([], "\n".join(sql_lines))
    for line in sql_lines:
        assert line not in banner
    assert banner == classify_unmarked_probe_failure(sql_lines)


_SAMPLED_MODELS = '''
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Appointment:
    scheduled_time: str = ""
    duration_minutes: int = 1
    status: str = "booked"
    service_type: str = ""
    channel: str = "email"
    FIELDS = ["scheduled_time", "duration_minutes", "status", "service_type", "channel"]
    # The channel's vocabulary is DECLARED; the sample follows it.
    CONSTRAINTS = {
        "status": {"allowed_values": ["booked", "completed"]},
        "scheduled_time": {"format": "time"},
        "channel": {"allowed_values": ["email", "sms"]},
    }


@dataclass
class Undeclared:
    """Same names, nothing declared: the name decides nothing."""
    channel: str = ""
    created_at: str = ""
    visit_date: str = ""
    owner_email: str = ""
    FIELDS = ["channel", "created_at", "visit_date", "owner_email"]
    CONSTRAINTS = {}


@dataclass
class DatetimeAnn:
    visit: Optional[datetime] = None
    FIELDS = ["visit"]
    CONSTRAINTS = {}


MODELS = {"appointment": Appointment, "undeclared": Undeclared, "datetime_ann": DatetimeAnn}
'''


def test_probe_value_samples_appointment_fields_not_the_word_sample(tmp_path):
    """The probe has no sampler of its own: it posts TESTER's base sample
    (base_samples -- the one builder over the product's declared models).
    The declared-field properties the probe's sampler used to pin hold on
    what it posts now."""
    from app.factory.build.payload_helpers import base_samples

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text(_SAMPLED_MODELS, encoding="utf-8")
    samples = base_samples(tmp_path)

    payload = samples["appointment"]
    assert payload["scheduled_time"] == "10:00:00"
    assert payload["scheduled_time"] != "sample"
    assert payload["duration_minutes"] == 1
    assert payload["status"] == "booked"
    assert payload["service_type"] == "sample"
    assert payload["channel"] == "email"  # allowed_values[0]

    assert set(samples["undeclared"].values()) == {"sample"}

    assert samples["datetime_ann"]["visit"] == "2026-09-03T10:00:00"


def _appointment_spec() -> dict:
    return {
        "end_to_end_appointment_workflow": {
            "entity": "appointment",
            "fields": [
                {"name": "scheduled_time", "type": "str", "required": True},
                {
                    "name": "duration_minutes",
                    "type": "int",
                    "required": True,
                    "min": 1,
                },
                {
                    "name": "status",
                    "type": "str",
                    "required": True,
                    "allowed_values": ["booked", "completed", "cancelled"],
                },
                {"name": "service_type", "type": "str", "required": True},
            ],
        }
    }


def _write_appointment_sql_workspace(root: Path, *, invalid_pk: bool = False) -> None:
    """Fixture matching the live vet appointment schema (sqlite + Alembic)."""
    from app.factory.build.data_lifecycle import (
        render_alembic_env,
        render_alembic_ini,
        render_migrations,
        render_revision_0001,
        render_revision_0002,
        render_script_mako,
        render_store,
    )
    from app.factory.build.roles_handlers import _render_models

    specs = _appointment_spec()
    app = root / "app"
    (app / "actions").mkdir(parents=True, exist_ok=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "models.py").write_text(_render_models(specs), encoding="utf-8")
    # db.py is substrate: the real emission writes it via platform_substrate,
    # and app/migrations.py imports it (app.db decides the engine). A harness
    # workspace without it is not the workspace the probe actually sees.
    from app.factory.build.engine_switch import render_db_module

    (app / "db.py").write_text(render_db_module(), encoding="utf-8")
    (app / "store.py").write_text(render_store(specs), encoding="utf-8")
    (app / "migrations.py").write_text(render_migrations(), encoding="utf-8")
    (root / "alembic.ini").write_text(render_alembic_ini(), encoding="utf-8")
    alembic = root / "alembic" / "versions"
    alembic.mkdir(parents=True, exist_ok=True)
    (root / "alembic" / "env.py").write_text(render_alembic_env(), encoding="utf-8")
    (root / "alembic" / "script.py.mako").write_text(
        render_script_mako(), encoding="utf-8"
    )
    rev = (
        _INVALID_APPOINTMENT_REVISION
        if invalid_pk
        else render_revision_0001(specs)
    )
    (alembic / "0001_baseline.py").write_text(rev, encoding="utf-8")
    (alembic / "0002_lifecycle_audit.py").write_text(
        render_revision_0002(), encoding="utf-8"
    )

    (app / "dispatch.py").write_text(
        "def execute(block_id, payload=None, action=None, params=None):\n"
        '    return {"status": "ok", "block": block_id}\n',
        encoding="utf-8",
    )
    (app / "jobs.py").write_text(
        "CAPABILITIES = ["
        "{'id': 'end_to_end_appointment_workflow', 'entity': 'appointment'}]\n"
        "JOBS = []\nCATALOG = {}\nGATES = {}\n",
        encoding="utf-8",
    )
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import end_to_end_appointment_workflow  # noqa: F401\n",
        encoding="utf-8",
    )
    (app / "actions" / "end_to_end_appointment_workflow.py").write_text(
        '"""Handler for capability end_to_end_appointment_workflow.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n'
        "from app.dispatch import execute\n\n"
        "CAPABILITY_ID = 'end_to_end_appointment_workflow'\n\n"
        "def handle(payload):\n"
        "    res = execute('database', {'input': payload}, action='insert')\n"
        "    if res.get('status') == 'error':\n"
        "        return {'ok': False, 'error': res.get('error')}\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID}\n",
        encoding="utf-8",
    )
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        "from app.actions import end_to_end_appointment_workflow as _action\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/end_to_end_appointment_workflow")\n'
        "def appointment_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        '    CAPABILITY_ID = "end_to_end_appointment_workflow"\n'
        "    result = _action.handle(payload)\n"
        '    if isinstance(result, dict) and result.get("ok") is False:\n'
        "        return result\n"
        '    save = lambda record: store.save("appointment", record, tenant_id="local")\n'
        '    saved = save(payload)\n'
        '    return {"ok": True, "capability": CAPABILITY_ID, "stored": saved}\n\n'
        '@router.get("/end_to_end_appointment_workflow")\n'
        "def appointment_list() -> Dict[str, Any]:\n"
        '    list_all = lambda: store.list_all("appointment", tenant_id="local")\n'
        '    items = list_all()\n'
        '    return {"items": items, "total": len(items)}\n',
        encoding="utf-8",
    )
    (app / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from app.routes import router\n"
        'app = FastAPI(title="appointment-probe")\n'
        'app.include_router(router, prefix="/v1")\n\n'
        '@app.get("/health")\n'
        "def health():\n"
        '    return {"status": "ok"}\n',
        encoding="utf-8",
    )

    with (app / "main.py").open("a", encoding="utf-8") as handle:
        handle.write(CAPABILITIES_ROUTE)
    static = app / "static"
    static.mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text(CONSOLE, encoding="utf-8")


def test_gate_passes_appointment_shaped_sqlite_workspace(tmp_path):
    """New-domain appointment schema must compile, migrate, and fail-closed."""
    _write_appointment_sql_workspace(tmp_path, invalid_pk=False)
    models = (tmp_path / "app" / "models.py").read_text(encoding="utf-8")
    assert "scheduled_time" in models
    assert "duration_minutes" in models
    rev = (tmp_path / "alembic" / "versions" / "0001_baseline.py").read_text(
        encoding="utf-8"
    )
    assert "scheduled_time" in rev
    assert "duration_minutes" in rev
    assert "service_type" in rev
    result = _run_gate(tmp_path)
    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != F1_HALT
    assert "scheduled_time TEXT" not in (result.detail or "")
    assert SCHEMA_SQL_HALT not in (result.detail or "")


def test_gate_invalid_appointment_ddl_is_gate_finding_not_sql_banner(tmp_path):
    """Broken PRIMARY KEY (id) without an id column must name the schema bug."""
    _write_appointment_sql_workspace(tmp_path, invalid_pk=True)
    result = _run_gate(tmp_path)
    assert result.ok is False, result
    assert result.gate == GATE_NAME
    assert "scheduled_time TEXT" not in result.detail
    assert result.detail != "scheduled_time TEXT,"
    joined = " ".join(result.findings) + " " + result.detail
    assert SCHEMA_SQL_HALT in joined
    assert result.detail != F1_HALT
    assert "success over a failed block" not in result.detail


# -- live makerspace-management action-in-payload (2026-09-04) ------------

_REFUSING_DISPATCH = '''def execute(block_id, payload=None, action=None, params=None):
    payload = payload if isinstance(payload, dict) else {}
    inner = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    if "action" in payload or "action" in inner:
        if action is None:
            return {"status": "error", "error": "Unknown action: None"}
        return {"status": "error", "error": "%s unknown field(s): action" % block_id}
    if action is None:
        return {"status": "error", "error": "Unknown action: None"}
    return {"status": "ok", "block": block_id}
'''

_BURIED_ACTION_BODY = """    results = {}
    errors = {}
    for block_id in BLOCK_IDS:
        body = dict(payload)
        body["action"] = BLOCK_DEFAULT_ACTIONS.get(block_id) or "run"
        res = execute(block_id, body)
        results[block_id] = res
        if isinstance(res, dict) and (res.get("status") == "error" or "error" in res):
            errors[block_id] = str(res.get("error") or res)[:200]
    if errors:
        return {"ok": False, "capability": CAPABILITY_ID,
                "error": "; ".join("%s: %s" % item for item in errors.items()),
                "results": results}
    return {"ok": True, "capability": CAPABILITY_ID, "results": results}
"""


def _write_makerspace_action_in_payload_workspace(root: Path, *, wrapped: bool) -> None:
    """Reproduce sess_39b5fec2abd346a5: action rides inside the payload.

    ``wrapped=True`` is the production WRITER path (``_handler_module`` +
    ``app/block_inputs.py``). ``wrapped=False`` is a raw execute() call that
    bypasses the repair wrapper — the honesty gate must still halt.
    """
    from app.factory.build.block_inputs import render_block_inputs_module
    from app.factory.build.roles_handlers import _handler_module

    _write_workspace(root, _GUARDED)
    app = root / "app"
    (app / "block_inputs.py").write_text(render_block_inputs_module(), encoding="utf-8")
    (app / "dispatch.py").write_text(_REFUSING_DISPATCH, encoding="utf-8")

    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            "MODELS = {"
            "'dashboards_and_reports': Widget, "
            "'equipment_inventory_and_maintenance': Widget}\n",
        ),
        encoding="utf-8",
    )
    (app / "jobs.py").write_text(
        "CAPABILITIES = ["
        "{'id': 'dashboards_and_reports', 'entity': 'dashboard'}, "
        "{'id': 'equipment_inventory_and_maintenance', 'entity': 'equipment'}]\n"
        "JOBS = []\nCATALOG = {}\nGATES = {}\n",
        encoding="utf-8",
    )
    defaults = {
        "dashboard": "render",
        "analytics": "track_event",
        "database": "insert",
        "estate_registry": "register",
    }
    (app / "actions" / "__init__.py").write_text(
        "from app.actions import dashboards_and_reports  # noqa: F401\n"
        "from app.actions import equipment_inventory_and_maintenance  # noqa: F401\n",
        encoding="utf-8",
    )
    if wrapped:
        (app / "actions" / "dashboards_and_reports.py").write_text(
            _handler_module(
                "dashboards_and_reports",
                ["dashboard", "analytics", "database"],
                _BURIED_ACTION_BODY,
                "coder LLM (makerspace live miss)",
                defaults,
            ),
            encoding="utf-8",
        )
        (app / "actions" / "equipment_inventory_and_maintenance.py").write_text(
            _handler_module(
                "equipment_inventory_and_maintenance",
                ["estate_registry"],
                _BURIED_ACTION_BODY,
                "coder LLM (makerspace live miss)",
                defaults,
            ),
            encoding="utf-8",
        )
    else:
        def _raw(cap: str, blocks: list[str]) -> str:
            return (
                "from app.dispatch import execute\n\n"
                f"CAPABILITY_ID = {cap!r}\n"
                f"BLOCK_IDS = {blocks!r}\n"
                f"BLOCK_DEFAULT_ACTIONS = {defaults!r}\n\n"
                "def handle(payload):\n"
                + _BURIED_ACTION_BODY
            )

        (app / "actions" / "dashboards_and_reports.py").write_text(
            _raw("dashboards_and_reports", ["dashboard", "analytics", "database"]),
            encoding="utf-8",
        )
        (app / "actions" / "equipment_inventory_and_maintenance.py").write_text(
            _raw("equipment_inventory_and_maintenance", ["estate_registry"]),
            encoding="utf-8",
        )
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        "from app.actions import dashboards_and_reports as _dash\n"
        "from app.actions import equipment_inventory_and_maintenance as _equip\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/dashboards_and_reports")\n'
        "def dashboards_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        "    result = _dash.handle(payload)\n"
        "    if isinstance(result, dict) and result.get('ok') is False:\n"
        "        return result\n"
        '    saved = store.save("dashboard", payload)\n'
        '    return {"ok": True, "capability": "dashboards_and_reports", "stored": saved}\n\n'
        '@router.get("/dashboards_and_reports")\n'
        "def dashboards_list() -> Dict[str, Any]:\n"
        '    items = store.list_all("dashboard")\n'
        '    return {"items": items, "total": len(items)}\n\n'
        '@router.post("/equipment_inventory_and_maintenance")\n'
        "def equipment_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
        "    result = _equip.handle(payload)\n"
        "    if isinstance(result, dict) and result.get('ok') is False:\n"
        "        return result\n"
        '    saved = store.save("equipment", payload)\n'
        '    return {"ok": True, "capability": "equipment_inventory_and_maintenance", "stored": saved}\n\n'
        '@router.get("/equipment_inventory_and_maintenance")\n'
        "def equipment_list() -> Dict[str, Any]:\n"
        '    items = store.list_all("equipment")\n'
        '    return {"items": items, "total": len(items)}\n',
        encoding="utf-8",
    )


def test_gate_repairs_action_buried_in_payload_and_does_not_halt(tmp_path):
    """Live makerspace: wrapped handlers must lift action= and ship a zip.

    The coder wrote execute(block, {..., "action": ...}) with no keyword.
    The fail-closed wrapper lifts and strips so blocks accept the call.
    Honesty is unchanged: Export still requires the rest of the pilot.
    """
    _write_makerspace_action_in_payload_workspace(tmp_path, wrapped=True)
    result = _run_gate(tmp_path)

    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != CONTRACT_HALT
    assert result.detail != F1_HALT
    assert CONTRACT_HALT not in (result.findings or [])
    joined = " ".join(result.findings or [])
    assert "the action travelled inside the payload" not in joined


def test_gate_still_halts_when_action_reaches_dispatch_inside_payload(tmp_path):
    """Honesty: an unrepaired action-in-payload still fails writer_behaviour.

    Per-capability findings must name the block and the live answers
    (Unknown action: None / unknown field(s): action). Export stays closed.
    """
    _write_makerspace_action_in_payload_workspace(tmp_path, wrapped=False)
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.detail == CONTRACT_HALT, result.detail
    assert CONTRACT_HALT in result.findings
    joined = " ".join(result.findings)
    assert "dashboards_and_reports" in joined
    assert "equipment_inventory_and_maintenance" in joined
    assert "the action travelled inside the payload" in joined
    assert "Unknown action" in joined or "unknown field" in joined.lower()
    assert result.detail != F1_HALT
    assert "success over a failed block" not in result.detail


# -- live veterinary-care required-field CONTRACT (2026-09-04) ------------

_LIVE_TOPIC = "RuntimeError: topic required"
_LIVE_DOC = (
    "RuntimeError: No input files provided (pdf/docx/xlsx). "
    "Pass file_path as pdf_path, docx_path, or xlsx_path."
)
_LIVE_SQL = "Query failed: missing sql or table"
_LIVE_TEAM = "Team access denied"

_VET_REFUSING_DISPATCH = '''import os

def execute(block_id, payload=None, action=None, params=None):
    payload = payload if isinstance(payload, dict) else {}
    if block_id == "event_bus":
        topic = payload.get("topic")
        if not (isinstance(topic, str) and topic.strip()):
            return {"status": "error", "error": "RuntimeError: topic required"}
    if block_id == "document_engine":
        paths = [payload.get(k) for k in ("file_path", "pdf_path", "docx_path", "xlsx_path")]
        if not any(isinstance(p, str) and os.path.isfile(p) for p in paths):
            return {"status": "error", "error": (
                "RuntimeError: No input files provided (pdf/docx/xlsx). "
                "Pass file_path as pdf_path, docx_path, or xlsx_path."
            )}
    if block_id == "database":
        sql = payload.get("sql")
        table = payload.get("table") or payload.get("table_name")
        if not ((isinstance(sql, str) and sql.strip()) or (isinstance(table, str) and table.strip())):
            return {"status": "error", "error": "Query failed: missing sql or table"}
    if block_id == "team":
        tid = payload.get("team_id")
        if not (isinstance(tid, str) and tid.startswith("team_") and len(tid) > 8):
            return {"status": "error", "error": "Team access denied"}
    return {"status": "ok", "block": block_id}
'''

_FORWARD_DOMAIN_BODY = """    results = {}
    errors = {}
    for block_id in BLOCK_IDS:
        res = execute(block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id))
        results[block_id] = res
        if isinstance(res, dict) and (res.get("status") == "error" or "error" in res):
            errors[block_id] = str(res.get("error") or res)[:200]
    if errors:
        return {"ok": False, "capability": CAPABILITY_ID,
                "error": "; ".join("%s: %s" % item for item in errors.items()),
                "results": results}
    return {"ok": True, "capability": CAPABILITY_ID, "results": results}
"""

_VET_CAPS = (
    ("automated_reminders", "reminder", ["event_bus"], {"event_bus": "publish"}),
    ("pet_records_management", "pet_record", ["document_engine"], {"document_engine": "parse"}),
    ("role_based_dashboard", "dashboard", ["database"], {"database": "query"}),
    ("veterinarian_availability", "availability", ["team"], {"team": "get_team_context"}),
)


def _write_veterinary_care_contract_workspace(root: Path, *, wrapped: bool) -> None:
    """Reproduce sess_a4aa977d2dff4c55: domain JSON, missing block records.

    ``wrapped=True`` is the production WRITER path. ``wrapped=False`` is a
    raw execute() that bypasses prepare_block_input — honesty must halt.
    """
    from app.factory.build.block_inputs import render_block_inputs_module
    from app.factory.build.roles_handlers import _handler_module

    _write_workspace(root, _GUARDED)
    app = root / "app"
    (app / "block_inputs.py").write_text(render_block_inputs_module(), encoding="utf-8")
    (app / "dispatch.py").write_text(_VET_REFUSING_DISPATCH, encoding="utf-8")
    (app / "preconditions.py").write_text(
        "RESOURCE_IDS = {'team': 'team_4f473e37589a69bb'}\n"
        "def resource_id(block_id):\n"
        "    return RESOURCE_IDS.get(block_id)\n"
        "def ensure_all():\n"
        "    return {'ids': dict(RESOURCE_IDS), 'errors': {}}\n",
        encoding="utf-8",
    )

    model_names = ", ".join(f"{cap!r}: Widget" for cap, _e, _b, _d in _VET_CAPS)
    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace("MODELS = {'widget_intake': Widget}\n", f"MODELS = {{{model_names}}}\n"),
        encoding="utf-8",
    )
    caps = ", ".join(
        f"{{'id': {cap!r}, 'entity': {entity!r}}}" for cap, entity, _b, _d in _VET_CAPS
    )
    (app / "jobs.py").write_text(
        f"CAPABILITIES = [{caps}]\nJOBS = []\nCATALOG = {{}}\nGATES = {{}}\n",
        encoding="utf-8",
    )
    imports = "\n".join(
        f"from app.actions import {cap}  # noqa: F401" for cap, _e, _b, _d in _VET_CAPS
    )
    (app / "actions" / "__init__.py").write_text(imports + "\n", encoding="utf-8")

    route_imports = "\n".join(
        f"from app.actions import {cap} as _{cap}" for cap, _e, _b, _d in _VET_CAPS
    )
    route_fns = []
    for cap, entity, _blocks, _defaults in _VET_CAPS:
        route_fns.append(
            f'@router.post("/{cap}")\n'
            f"def {cap}_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
            f"    result = _{cap}.handle(payload)\n"
            f"    if isinstance(result, dict) and result.get('ok') is False:\n"
            f"        return result\n"
            f'    saved = store.save("{entity}", payload)\n'
            f'    return {{"ok": True, "capability": "{cap}", "stored": saved}}\n\n'
            f'@router.get("/{cap}")\n'
            f"def {cap}_list() -> Dict[str, Any]:\n"
            f'    items = store.list_all("{entity}")\n'
            f'    return {{"items": items, "total": len(items)}}\n'
        )
    (app / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        f"{route_imports}\n\n"
        "router = APIRouter()\n\n"
        + "\n".join(route_fns),
        encoding="utf-8",
    )

    for cap, _entity, blocks, defaults in _VET_CAPS:
        if wrapped:
            text = _handler_module(
                cap, blocks, _FORWARD_DOMAIN_BODY,
                "coder LLM (veterinary-care live miss)", defaults,
            )
        else:
            text = (
                "from app.dispatch import execute\n\n"
                f"CAPABILITY_ID = {cap!r}\n"
                f"BLOCK_IDS = {blocks!r}\n"
                f"BLOCK_DEFAULT_ACTIONS = {defaults!r}\n\n"
                "def handle(payload):\n"
                + _FORWARD_DOMAIN_BODY
            )
        (app / "actions" / f"{cap}.py").write_text(text, encoding="utf-8")


def test_gate_repairs_veterinary_care_contract_fields_and_does_not_halt(tmp_path):
    """Live VetCare Hub: wrapper + prepare_block_input must not CONTRACT_HALT."""
    _write_veterinary_care_contract_workspace(tmp_path, wrapped=True)
    result = _run_gate(tmp_path)

    assert result.ok is True, (result.detail, result.findings)
    assert result.detail != CONTRACT_HALT
    assert result.detail != F1_HALT
    assert CONTRACT_HALT not in (result.findings or [])
    joined = " ".join(result.findings or [])
    assert _LIVE_TOPIC not in joined
    assert "No input files provided" not in joined
    assert _LIVE_SQL not in joined
    assert _LIVE_TEAM not in joined


def test_gate_still_halts_when_live_contract_fields_are_unrepaired(tmp_path):
    """Honesty: unrepaired domain JSON still fails writer_behaviour.

    Per-capability findings must quote the four live Store answers.
    Export stays closed — this gate failure is not pilot_ready.
    """
    _write_veterinary_care_contract_workspace(tmp_path, wrapped=False)
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    assert result.detail == CONTRACT_HALT, result.detail
    assert CONTRACT_HALT in result.findings
    joined = " ".join(result.findings)
    assert "automated_reminders" in joined
    assert "pet_records_management" in joined
    assert "role_based_dashboard" in joined
    assert "veterinarian_availability" in joined
    assert _LIVE_TOPIC in joined
    assert "No input files provided" in joined
    assert _LIVE_SQL in joined
    assert _LIVE_TEAM in joined
    assert result.detail != F1_HALT
    assert "success over a failed block" not in result.detail
