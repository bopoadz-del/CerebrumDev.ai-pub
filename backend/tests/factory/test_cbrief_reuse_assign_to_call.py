"""C-BRIEF: CLONER result-key rewrite must not emit illegal assignment.

Live photograph (sess_c63cc1a274994b33, tip 467c83e / #350):
#350 cleared appointment_scheduling workflow RuntimeError: 'result'
(0 hits). NEW all-REUSE VetCare / VetClinic Hub (5× REUSE) reached
TESTER then STOPPED after rework×3:

    appointment_scheduling rejected a payload built from its own schema:
    queue: SyntaxError: cannot assign to function call  (queue.py ~line 189)
    workflow: step_0 (event_bus): error
    billing_and_invoicing: formula_executor same SyntaxError (~line 242)
    schema sample refused; accept-payload persisted nothing

#350 rewrote any identifier ['result'] to .get("result", obj), including
assignment targets. Store queue / formula_executor assign that key
(``something(x) = ...``). This is emit / CLONER, not a per-cap handle()
micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
Do not steal vector_search BLOCK_DEFAULT_ACTIONS work.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.block_inputs import render_block_inputs_module
from app.factory.build.brief_compiler import compile_brief, verify_inventory
from app.factory.build.coder_session import emit_factory_grounded_reuse_keep_path
from app.factory.build.offline_adapters import emit_result_key_access
from app.factory.build.reuse_accept import (
    PRODUCT_ASSIGN_TO_CALL_HALT,
)
from app.factory.build.roles_handlers import (
    _prepare_cloned_python,
    _sample_payload,
)
from tests.factory.test_cbrief_reuse_schema_accept import (
    SAMPLE_REUSE_PLAN_BLOCKS,
    STORE_IDS,
    _VetCare,
    _Cap,
    _Plan,
)

#: #350 substitution — the live illegal rewrite.
_NAIVE_RESULT_KEY_SUB = re.compile(
    r"""\b([A-Za-z_][\w]*)\s*\[\s*['\"]result['\"]\s*\]"""
)

_STORE_QUEUE_ASSIGN = textwrap.dedent(
    '''
    """Store-like queue shim (sess_c63cc1a photograph ~line 189)."""

    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        result = {"status": "ok", "block_id": "queue"}
        result["result"] = payload if isinstance(payload, dict) else {"value": payload}
        return result
    '''
)

_STORE_FORMULA_ASSIGN = textwrap.dedent(
    '''
    """Store-like formula_executor shim (sess_c63cc1a photograph ~line 242)."""

    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        output = {"status": "ok", "block_id": "formula_executor"}
        output["result"] = payload if isinstance(payload, dict) else {"value": payload}
        return output
    '''
)

_EVENT_BUS_OK = textwrap.dedent(
    """
    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        return {"block_id": "event_bus", "status": "ok"}
    """
)

_WORKFLOW_OK = textwrap.dedent(
    """
    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        return {"block_id": "workflow", "status": "ok", "result": payload.get("result")}
    """
)

_ANALYTICS_OK = textwrap.dedent(
    """
    def run(**kwargs):
        return {"block_id": "analytics", "status": "ok"}
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
        try:
            spec.loader.exec_module(module)
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
    cap = sys.argv[1]
    sample = json.loads(sys.argv[2])
    mod = importlib.import_module("app.actions." + cap)
    result = mod.handle(sample)
    from app import store
    # Phase 2 §0.2: persistence is route-scoped. Simulate the emitted
    # route: persist the request payload only after a successful handle().
    if isinstance(result, dict) and result.get("ok") is not False:
        store.save(cap, sample)
    rows = store.list_all(cap)
    print(json.dumps({"result": result, "rows": rows}, default=str))
    """
)


def _naive_rewrite(text: str) -> str:
    return _NAIVE_RESULT_KEY_SUB.sub(r'\1.get("result", \1)', text)


def _schema_sample() -> dict:
    return _sample_payload(
        {
            "fields": [
                {"name": "reference", "type": "str", "required": True},
                {"name": "status", "type": "str", "required": True},
            ]
        }
    )


def _plant_json(root: Path, block_id: str, default: str) -> None:
    dest = root / "vendor" / "blocks" / block_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "block.json").write_text(
        json.dumps(
            {
                "id": block_id,
                "inputs": [
                    {
                        "name": "action",
                        "type": "string",
                        "default": default,
                        "options": [default],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _plant_block(root: Path, block_id: str, source: str) -> None:
    dest = root / "vendor" / "blocks" / block_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "block.py").write_text(source, encoding="utf-8")


def _live_plan() -> _Plan:
    """sess_c63cc1a: appointment_scheduling bound queue + event_bus + workflow."""
    blocks = dict(SAMPLE_REUSE_PLAN_BLOCKS)
    blocks["appointment_scheduling"] = ["event_bus", "workflow", "queue"]
    return _Plan(*(_Cap(cid, bids) for cid, bids in blocks.items()))


def _emit_keep_path(root: Path) -> None:
    store_ids = set(STORE_IDS) | {"queue"}
    compiled = compile_brief(_VetCare(), _live_plan(), store_ids=store_ids)
    verify_inventory(compiled)
    for bid, default in (
        ("database", "query"),
        ("validation", "validate"),
        ("event_bus", "publish"),
        ("workflow", "run"),
        ("analytics", "track_event"),
        ("team", "create_team"),
        ("formula_executor", "execute"),
        ("queue", "enqueue"),
    ):
        _plant_json(root, bid, default)
    emit_factory_grounded_reuse_keep_path(root, compiled)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "dispatch.py").write_text(_MINI_DISPATCH, encoding="utf-8")
    (root / "app" / "store.py").write_text(_MINI_STORE, encoding="utf-8")
    (root / "app" / "block_inputs.py").write_text(
        render_block_inputs_module(), encoding="utf-8"
    )
    (root / "app" / "actions" / "__init__.py").write_text("", encoding="utf-8")


def _run_handle(root: Path, capability: str, sample: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _HANDLE_SCRIPT, capability, json.dumps(sample)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout.strip())


def test_naive_result_key_rewrite_photographs_assign_to_call():
    """Photograph #350: assignment rewrite is SyntaxError: cannot assign to function call."""
    for raw, name in (
        (_STORE_QUEUE_ASSIGN, "queue"),
        (_STORE_FORMULA_ASSIGN, "formula_executor"),
    ):
        ast.parse(raw)
        naive = _naive_rewrite(raw)
        assert '.get("result"' in naive
        try:
            ast.parse(naive)
        except SyntaxError as exc:
            assert "cannot assign to function call" in str(exc)
            assert "cannot assign to function call" in PRODUCT_ASSIGN_TO_CALL_HALT
        else:
            raise AssertionError(
                f"{name}: naive #350 rewrite must photograph SyntaxError"
            )


def test_emit_result_key_access_keeps_assignment_valid():
    """Fixed emit: assignment stays a subscript; reads still .get()."""
    for raw in (_STORE_QUEUE_ASSIGN, _STORE_FORMULA_ASSIGN):
        rewritten = emit_result_key_access(raw)
        ast.parse(rewritten)
        assert '["result"] =' in rewritten or "['result'] =" in rewritten
        assert ".get(\"result\"" not in rewritten or (
            rewritten.count(".get(\"result\"") == rewritten.count("return")
        )
        ns: dict = {}
        exec(rewritten, ns, ns)
        out = ns["run"](input={"reference": "sample", "status": "open"})
        assert out.get("status") == "ok"
        assert out.get("result")

    read = "value = step_result['result']\n"
    assert 'step_result.get("result", step_result)' in emit_result_key_access(read)

    assign_and_read = (
        "out = {'status': 'ok'}\n"
        "out['result'] = {'kept': True}\n"
        "return out['result']\n"
    )
    mixed = emit_result_key_access(assign_and_read)
    assert "out['result'] =" in mixed or 'out["result"] =' in mixed
    assert 'out.get("result", out)' in mixed


def test_prepare_cloned_python_queue_formula_compile():
    """CLONER _prepare_cloned_python must ship valid queue / formula_executor."""
    for raw in (_STORE_QUEUE_ASSIGN, _STORE_FORMULA_ASSIGN):
        shipped = _prepare_cloned_python(raw)
        ast.parse(shipped)
        assert "cannot assign to function call" not in shipped
        ns: dict = {}
        exec(shipped, ns, ns)
        out = ns["run"](input={"reference": "sample"})
        assert out.get("status") == "ok"


def test_mutation_naive_rewrite_refuses_schema_sample(tmp_path):
    """Plant the live illegal rewrite: schema-sample must surface SyntaxError."""
    sample = _schema_sample()
    _emit_keep_path(tmp_path)
    _plant_block(tmp_path, "queue", _naive_rewrite(_STORE_QUEUE_ASSIGN))
    _plant_block(tmp_path, "formula_executor", _naive_rewrite(_STORE_FORMULA_ASSIGN))
    _plant_block(tmp_path, "event_bus", _EVENT_BUS_OK)
    _plant_block(tmp_path, "workflow", _WORKFLOW_OK)
    _plant_block(tmp_path, "analytics", _ANALYTICS_OK)

    sched = _run_handle(tmp_path, "appointment_scheduling", sample)
    sched_err = str(sched["result"].get("error") or sched["result"])
    assert "SyntaxError" in sched_err
    assert "cannot assign to function call" in sched_err
    assert not sched["rows"], "illegal rewrite must persist nothing"

    billing = _run_handle(tmp_path, "billing_and_invoicing", sample)
    bill_err = str(billing["result"].get("error") or billing["result"])
    assert "SyntaxError" in bill_err
    assert "cannot assign to function call" in bill_err
    assert not billing["rows"]


def test_keep_path_schema_sample_persists_after_cloner_rewrite(tmp_path):
    """REUSE keep-path schema-sample persist against Store-like assign shims."""
    sample = _schema_sample()
    assert "result" not in sample
    _emit_keep_path(tmp_path)
    _plant_block(tmp_path, "queue", emit_result_key_access(_STORE_QUEUE_ASSIGN))
    _plant_block(
        tmp_path, "formula_executor", emit_result_key_access(_STORE_FORMULA_ASSIGN)
    )
    _plant_block(tmp_path, "event_bus", _EVENT_BUS_OK)
    _plant_block(tmp_path, "workflow", emit_result_key_access(_WORKFLOW_OK))
    _plant_block(tmp_path, "analytics", _ANALYTICS_OK)

    sched = _run_handle(tmp_path, "appointment_scheduling", sample)
    result = sched["result"]
    assert result.get("ok") is not False, result
    assert sched["rows"], "accept-payload persisted nothing"
    assert PRODUCT_ASSIGN_TO_CALL_HALT not in str(result)
    assert "SyntaxError" not in str(result.get("error") or "")

    billing = _run_handle(tmp_path, "billing_and_invoicing", sample)
    result = billing["result"]
    assert result.get("ok") is not False, result
    assert billing["rows"], "accept-payload persisted nothing"
    assert PRODUCT_ASSIGN_TO_CALL_HALT not in str(result)
    assert "SyntaxError" not in str(result.get("error") or "")
