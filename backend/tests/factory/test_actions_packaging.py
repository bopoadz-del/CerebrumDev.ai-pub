"""Circular ``app.actions`` packaging must not reach live Floor as an import crash.

Live sess_8273a26a4c1b4ee7 (VetCare Hub / veterinary-care) stopped at WRITER
``writer_behaviour``:

    workspace does not import: ImportError: cannot import name
    'pet_records_management' from partially initialized module 'app.actions'
    (.../app/actions/__init__.py)

Root cause: factory ``app/actions/__init__.py`` and ``app/routes.py`` both
did ``from app.actions import pet_records_management`` at load time. A
CLI/Kimi handler that imports ``app.routes`` (or ``app.main``) completes
the cycle. The probe never reached route honesty. This file is the
failing-then-passing lock:

* eager init + eager route fromlist + handler ``import app.routes`` is circular
* ``writer_behaviour`` still fail-closes on that import (not ignored)
* factory lazy init + deferred route imports import cleanly with the same
  handlers
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.factory.build.brief_compiler import compile_brief
from app.factory.build.roles_handlers import (
    _render_actions_init,
    _render_routes,
    actions_init_eager_reexports,
    render_routes_files,
)
from app.factory.build.writer_behaviour import F1_HALT
from app.factory.product_architect import plan_blueprint
from app.factory.blueprint import load_blueprint

from tests.factory.test_writer_behaviour_gate import (
    _GUARDED,
    _VET_CAPS,
    _run_gate,
    _write_workspace,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


# Kimi-class full module: handle() plus a load-time import of the HTTP
# surface. Combined with eager package/route fromlist this is the cycle.
_HANDLER_IMPORTS_ROUTES = """
from app.dispatch import execute
from app.routes import router

CAPABILITY_ID = {cap!r}

def handle(payload):
    _ = router
    res = execute('database', {{'input': payload}}, action='insert')
    if res.get('status') == 'error':
        return {{'ok': False, 'error': res.get('error')}}
    return {{'ok': True, 'capability': CAPABILITY_ID}}
"""


def _eager_actions_init(names) -> str:
    ordered = ["pet_records_management"] + [n for n in names if n != "pet_records_management"]
    return (
        '"""Capability handlers."""\n\n'
        + "\n".join(f"from app.actions import {n}  # noqa: F401" for n in ordered)
        + "\n"
    )


def _write_vetcare_circular_handlers(root: Path) -> None:
    """Kimi-class handlers that import ``app.routes`` at module load."""
    app = root / "app"
    for cap, _entity, _blocks, _defaults in _VET_CAPS:
        (app / "actions" / f"{cap}.py").write_text(
            _HANDLER_IMPORTS_ROUTES.format(cap=cap),
            encoding="utf-8",
        )


def _overlay_vetcare_caps(root: Path) -> list[str]:
    app = root / "app"
    names = [cap for cap, _e, _b, _d in _VET_CAPS]
    model_names = ", ".join(f"{cap!r}: Widget" for cap, _e, _b, _d in _VET_CAPS)
    models = (app / "models.py").read_text(encoding="utf-8")
    (app / "models.py").write_text(
        models.replace(
            "MODELS = {'widget_intake': Widget}\n",
            f"MODELS = {{{model_names}}}\n",
        ),
        encoding="utf-8",
    )
    caps = ", ".join(
        f"{{'id': {cap!r}, 'entity': {entity!r}}}"
        for cap, entity, _b, _d in _VET_CAPS
    )
    (app / "jobs.py").write_text(
        f"CAPABILITIES = [{caps}]\nJOBS = []\nCATALOG = {{}}\nGATES = {{}}\n",
        encoding="utf-8",
    )
    return names


def _write_eager_routes(root: Path, names: list[str]) -> None:
    """Pre-fix factory routes: module-level ``from app.actions import``."""
    # Live Floor named pet_records_management — import that capability first.
    ordered = ["pet_records_management"] + [n for n in names if n != "pet_records_management"]
    imports = "\n".join(
        f"from app.actions import {n} as _{n}" for n in ordered
    )
    fns = []
    entities = {cap: entity for cap, entity, _b, _d in _VET_CAPS}
    for n in names:
        entity = entities[n]
        fns.append(
            f'@router.post("/{n}")\n'
            f"def {n}_create(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
            f"    result = _{n}.handle(payload)\n"
            f"    if isinstance(result, dict) and result.get('ok') is False:\n"
            f"        return result\n"
            f'    saved = store.save("{entity}", payload)\n'
            f'    return {{"ok": True, "capability": "{n}", "stored": saved}}\n\n'
            f'@router.get("/{n}")\n'
            f"def {n}_list() -> Dict[str, Any]:\n"
            f'    items = store.list_all("{entity}")\n'
            f'    return {{"items": items, "total": len(items)}}\n'
        )
    (root / "app" / "routes.py").write_text(
        "from typing import Any, Dict\n"
        "from fastapi import APIRouter\n"
        "from app import store\n"
        f"{imports}\n\n"
        "router = APIRouter()\n\n"
        + "\n".join(fns),
        encoding="utf-8",
    )


def _write_live_circular_workspace(root: Path) -> list[str]:
    """Reproduce the Floor import crash (eager init + handler→routes)."""
    _write_workspace(root, _GUARDED)
    names = _overlay_vetcare_caps(root)
    (root / "app" / "actions" / "__init__.py").write_text(
        _eager_actions_init(names), encoding="utf-8"
    )
    _write_eager_routes(root, names)
    _write_vetcare_circular_handlers(root)
    return names


def _write_factory_packaged_workspace(root: Path) -> list[str]:
    """Same sibling-importing handlers; factory init + deferred routes."""
    _write_workspace(root, _GUARDED)
    names = _overlay_vetcare_caps(root)
    (root / "app" / "actions" / "__init__.py").write_text(
        _render_actions_init(names), encoding="utf-8"
    )
    entries = [
        {
            "capability_id": cap,
            "name": cap,
            "entity": entity,
            "body": _GUARDED,
            "source": "factory packaging fixture",
        }
        for cap, entity, _b, _d in _VET_CAPS
    ]
    # Routes and the Factory modules they import are one unit.
    for rel, text in render_routes_files(entries, None).items():
        (root / rel).write_text(text, encoding="utf-8")
    _write_vetcare_circular_handlers(root)
    # Factory routes import jobs / kernel_bridge / domain_ops. Stub the
    # extras the probe does not need so packaging is the variable.
    app = root / "app"
    (app / "domain_ops.py").write_text(
        "async def perform(*_a, **_k):\n"
        "    return {'status': 'success'}\n",
        encoding="utf-8",
    )
    (app / "kernel_bridge.py").write_text(
        "async def run_capability(capability_id, payload):\n"
        "    return {'status': 'success', 'capability': capability_id}\n",
        encoding="utf-8",
    )
    jobs = (app / "jobs.py").read_text(encoding="utf-8")
    if "def inventory" not in jobs:
        (app / "jobs.py").write_text(
            jobs
            + "\ndef inventory():\n    return {'kernel': 'CLONER'}\n"
            + "def provenance():\n    return {'kernel': 'STORE_MANAGER'}\n",
            encoding="utf-8",
        )
    store = (app / "store.py").read_text(encoding="utf-8")
    if "COLUMNS" not in store:
        cols = {entity: ["id", "name"] for _c, entity, _b, _d in _VET_CAPS}
        (app / "store.py").write_text(
            store
            + f"\nCOLUMNS = {cols!r}\n"
            + "class QueryError(Exception):\n    pass\n"
            + "def query(entity, **_k):\n    return {'items': list_all(entity), 'total': 0}\n"
            + "def get(entity, item_id):\n    return None\n",
            encoding="utf-8",
        )
    return names


def _import_probe(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.main import app\n"
            "from app.actions import pet_records_management\n"
            "assert pet_records_management.CAPABILITY_ID\n",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_eager_actions_init_plus_handler_route_import_is_circular(tmp_path):
    """Failing fixture: the live ``app.actions`` packaging class.

    Eager ``from app.actions import pet_records_management`` in ``__init__.py``
    and in ``routes.py``, plus a handler that imports ``app.routes``, is the
    cycle. The traceback names that fromlist; the ImportError is the
    partially initialized module (``app.actions`` or ``app.routes``).
    """
    _write_live_circular_workspace(tmp_path)
    init = (tmp_path / "app" / "actions" / "__init__.py").read_text(encoding="utf-8")
    assert "pet_records_management" in actions_init_eager_reexports(init)

    proc = _import_probe(tmp_path)
    assert proc.returncode != 0, proc.stdout
    err = proc.stderr
    assert "pet_records_management" in err, err
    assert "partially initialized" in err, err
    assert "from app.actions import pet_records_management" in err, err


def test_writer_behaviour_fail_closes_on_circular_actions_import(tmp_path):
    """Honesty: the gate still names the import crash. Do not ignore it."""
    _write_live_circular_workspace(tmp_path)
    result = _run_gate(tmp_path)

    assert result.ok is False, result
    joined = " ".join([result.detail or "", *(result.findings or [])])
    assert "workspace does not import" in joined, joined
    assert "partially initialized" in joined, joined
    assert result.detail != F1_HALT
    assert "success over a failed block" not in (result.detail or "")


def test_factory_actions_init_has_no_eager_reexport():
    text = _render_actions_init(
        ["automated_reminders", "pet_records_management"]
    )
    assert actions_init_eager_reexports(text) == []
    assert "from app.actions import" not in text
    assert "__getattr__" in text
    assert "pet_records_management" in text
    assert "importlib.import_module" in text


def test_factory_routes_defer_action_imports():
    src = _render_routes(
        [
            {
                "capability_id": "pet_records_management",
                "name": "pet_records_management",
                "entity": "pet_record",
                "body": '    return {"ok": True}',
                "source": "template",
            }
        ]
    )
    assert "from app.actions import pet_records_management" not in src
    assert "from app.actions.pet_records_management import handle" in src


def test_factory_packaging_imports_veterinary_care_with_handler_route_imports(
    tmp_path,
):
    """Passing fixture: factory emit + the same Kimi-class route imports."""
    _write_factory_packaged_workspace(tmp_path)
    init = (tmp_path / "app" / "actions" / "__init__.py").read_text(encoding="utf-8")
    assert actions_init_eager_reexports(init) == []

    proc = _import_probe(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "partially initialized" not in proc.stderr

    result = _run_gate(tmp_path)
    joined = " ".join([result.detail or "", *(result.findings or [])])
    assert "workspace does not import" not in joined, joined
    assert "partially initialized" not in joined, joined


def test_compiled_brief_carries_actions_packaging_contract():
    bp = load_blueprint(SMOKE)
    compiled = compile_brief(bp, plan_blueprint(bp))
    # The factory owns the actions package; the brief says so.
    assert "app/actions/__init__.py" in compiled.text


