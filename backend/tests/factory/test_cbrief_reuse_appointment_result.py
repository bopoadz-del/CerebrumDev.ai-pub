"""C-BRIEF: ALL-REUSE appointment_scheduling schema-sample must persist.

Live photograph (sess_07dff0eaf8f64186, tip da7cd2b / #348):
Unknown action / formula_executor / ModuleNotFoundError did not recur.
TESTER PRODUCT then failed after rework budget 3:

    appointment_scheduling rejected a payload built from its own schema:
    workflow: RuntimeError: 'result'
    schema sample refused; accept-payload persisted nothing

Store workflow / kit shim reads input['result'] or out['result'] and
wraps the KeyError as RuntimeError. The PRODUCT schema-sample POST has
pet/date/status — no result key. This is prepare + emit + CLONER rewrite,
not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.block_inputs import (
    prepare_block_input,
    render_block_inputs_module,
)
from app.factory.build.brief_compiler import compile_brief, verify_inventory
from app.factory.build.coder_session import emit_factory_grounded_reuse_keep_path
from app.factory.build.offline_adapters import emit_result_key_access
from app.factory.build.roles_handlers import _sample_payload
from app.factory.build.workflow_accept import PRODUCT_WORKFLOW_RESULT_HALT
from tests.factory.test_cbrief_reuse_schema_accept import (
    STORE_IDS,
    _VetCare,
    _plant_store_block_json,
    _vetcare_reuse_plan,
)

_STORE_WORKFLOW_OUT_RESULT = textwrap.dedent(
    """
    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        steps = payload.get("steps") or []
        out = {
            "status": "success",
            "results": [{"block": (s or {}).get("block")} for s in steps],
        }
        try:
            return out["result"]
        except KeyError as exc:
            raise RuntimeError(exc) from exc
    """
)

_STORE_WORKFLOW_INPUT_RESULT = textwrap.dedent(
    """
    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        try:
            value = payload["result"]
        except KeyError as exc:
            raise RuntimeError(exc) from exc
        return {"status": "ok", "result": value}
    """
)

_EVENT_BUS_NO_RESULT = textwrap.dedent(
    """
    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        return {"block_id": "event_bus", "status": "ok"}
    """
)

_MINI_DISPATCH = textwrap.dedent(
    """
    import importlib.util
    from pathlib import Path

    def execute(block_id, payload=None, action=None, params=None, **kw):
        path = Path("vendor/blocks") / block_id / "block.py"
        spec = importlib.util.spec_from_file_location("vendored_" + block_id, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            result = module.run(input=payload or {}, action=action, **(params or {}))
        except Exception as exc:
            return {
                "status": "error",
                "error": "%s: %s" % (type(exc).__name__, exc),
                "ok": False,
            }
        if isinstance(result, dict) and (
            result.get("status") == "error" or "error" in result
        ):
            return result
        return result if isinstance(result, dict) else {"status": "ok", "result": result}
    """
)

_MINI_STORE = textwrap.dedent(
    """
    _ROWS = {}

    def save(entity, record):
        rows = _ROWS.setdefault(entity, [])
        stored = dict(record)
        stored.setdefault("id", len(rows) + 1)
        rows.append(stored)
        return stored

    def list_all(entity):
        return list(_ROWS.get(entity, []))
    """
)

_HANDLE_SCRIPT = textwrap.dedent(
    """
    import importlib, json, sys
    sys.path.insert(0, ".")
    sample = json.loads(sys.argv[1])
    mod = importlib.import_module("app.actions.appointment_scheduling")
    result = mod.handle(sample)
    from app import store
    # Phase 2 §0.2: persistence is route-scoped. Simulate the emitted
    # route: persist the request payload only after a successful handle().
    if isinstance(result, dict) and result.get("ok") is not False:
        store.save("appointment_scheduling", sample)
    rows = store.list_all("appointment_scheduling")
    print(json.dumps({"result": result, "rows": rows}, default=str))
    """
)


def _schema_sample() -> dict:
    return _sample_payload(
        {
            "fields": [
                {"name": "reference", "type": "str", "required": True},
                {"name": "status", "type": "str", "required": True},
            ]
        }
    )


def _emit_scheduling(root: Path) -> None:
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    verify_inventory(compiled)
    _plant_store_block_json(root)
    emit_factory_grounded_reuse_keep_path(root, compiled)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "dispatch.py").write_text(_MINI_DISPATCH, encoding="utf-8")
    (root / "app" / "store.py").write_text(_MINI_STORE, encoding="utf-8")
    (root / "app" / "block_inputs.py").write_text(
        render_block_inputs_module(), encoding="utf-8"
    )
    (root / "app" / "actions" / "__init__.py").write_text("", encoding="utf-8")


def _plant_block(root: Path, block_id: str, source: str) -> None:
    dest = root / "vendor" / "blocks" / block_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "block.py").write_text(source, encoding="utf-8")


def _run_handle(root: Path, sample: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _HANDLE_SCRIPT, json.dumps(sample)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout.strip())


def test_mutation_unrewritten_out_result_is_runtimeerror_result():
    """Photograph the live Store shim before CLONER rewrite."""
    raw = _STORE_WORKFLOW_OUT_RESULT
    assert 'out["result"]' in raw
    try:
        ns: dict = {}
        exec(raw, ns, ns)
        ns["run"](input={"steps": [{"block": "event_bus"}]})
    except RuntimeError as exc:
        assert str(exc) == "'result'"
    else:
        raise AssertionError("unrewritten Store shim must raise RuntimeError: 'result'")
    rewritten = emit_result_key_access(raw)
    assert 'out.get("result", out)' in rewritten
    ns = {}
    exec(rewritten, ns, ns)
    out = ns["run"](input={"steps": [{"block": "event_bus"}]})
    assert out.get("status") == "success"
    assert out.get("results")[0]["block"] == "event_bus"


def test_keep_path_schema_sample_persists_when_store_workflow_reads_result(
    tmp_path,
):
    """Keep-path handle(schema-sample) must persist against Store-like workflow."""
    sample = _schema_sample()
    assert "result" not in sample
    _emit_scheduling(tmp_path)
    _plant_block(
        tmp_path, "workflow", emit_result_key_access(_STORE_WORKFLOW_OUT_RESULT)
    )
    _plant_block(tmp_path, "event_bus", _EVENT_BUS_NO_RESULT)
    body = _run_handle(tmp_path, sample)
    result = body["result"]
    assert result.get("ok") is not False, result
    assert body["rows"], "accept-payload persisted nothing"
    assert PRODUCT_WORKFLOW_RESULT_HALT not in str(result)
    assert "RuntimeError" not in str(result.get("error") or "")


def test_keep_path_schema_sample_persists_when_workflow_requires_input_result(
    tmp_path,
):
    """prepare_block_input fills result so input['result'] does not KeyError."""
    sample = _schema_sample()
    prepared = prepare_block_input(
        "workflow", sample, roster=["workflow", "event_bus"]
    )
    assert prepared.get("result") not in (None, "")
    _emit_scheduling(tmp_path)
    _plant_block(tmp_path, "workflow", _STORE_WORKFLOW_INPUT_RESULT)
    _plant_block(tmp_path, "event_bus", _EVENT_BUS_NO_RESULT)
    body = _run_handle(tmp_path, sample)
    result = body["result"]
    assert result.get("ok") is not False, result
    assert body["rows"], "accept-payload persisted nothing"


def test_mutation_input_result_miss_is_the_live_halt():
    """Dropping prepare's result key reproduces RuntimeError: 'result'."""
    ns: dict = {}
    exec(_STORE_WORKFLOW_INPUT_RESULT, ns, ns)
    try:
        ns["run"](input={"steps": [{"block": "event_bus"}], "reference": "sample"})
    except RuntimeError as exc:
        assert str(exc) == "'result'"
    else:
        raise AssertionError("schema-sample without result must raise")
    ok = ns["run"](input={"result": {"reference": "sample"}})
    assert ok.get("status") == "ok"
