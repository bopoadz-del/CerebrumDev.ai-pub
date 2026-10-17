"""C-BRIEF: after #352, schema-sample persist AND assign-to-call both hold.

Live photograph (sess_aed3e6e288414fcf, tip 0963a6b / #352 launching-ready
0639, VetClinic Hub ALL-REUSE):

    Caps REUSE×5: patient_records_management, appointment_scheduling,
    prescription_management, billing_and_invoicing,
    client_communication_portal
    #351 vector_search Unknown action: CLEARED
    #352 SyntaxError cannot assign to function call: CLEARED (did NOT recur)
    STOPPED TESTER rework×3:
        appointment_scheduling rejected a payload built from its own schema:
        workflow: RuntimeError: 'result'

#352 rewrote reads only and fail-closed *kept the whole original module*
when any rewrite did not compile. Store-ctx misses the suffix heuristic
(``for name['result'] in …``) then left workflow ``envelope['result']`` /
``input['result']`` as KeyError → RuntimeError: 'result'.

Photograph BOTH walls:
1. RuntimeError: 'result' if result is not attached / reads not rewritten
2. SyntaxError: cannot assign to function call if assignment is rewritten

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
Do not undo #351 vector_search harvest or #352 read-only rewrite.
"""

from __future__ import annotations

import ast
import json
import re
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
from app.factory.build.reuse_accept import (
    PRODUCT_ASSIGN_TO_CALL_HALT,
)
from app.factory.build.roles_handlers import (
    _prepare_cloned_python,
    _sample_payload,
)
from app.factory.build.workflow_accept import PRODUCT_WORKFLOW_RESULT_HALT
from tests.factory.test_cbrief_reuse_schema_accept import (
    STORE_IDS,
    _VetCare,
    _plant_store_block_json,
    _vetcare_reuse_plan,
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

#: Store workflow that READS input['result'] (KeyError → RuntimeError).
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

#: Store workflow that READS envelope['result'] AND has a Store-ctx target
#: the #352 suffix heuristic misses (for-loop). Whole-module fail-closed
#: kept this file original and the read KeyError'd.
_STORE_WORKFLOW_MIXED_FOR_STORE = textwrap.dedent(
    """
    def run(**kwargs):
        payload = kwargs.get("input", kwargs)
        out = {"status": "ok", "block_id": "workflow"}
        rows = payload.get("drain") or []
        for out["result"] in rows:
            pass
        envelope = {"status": "ok"}
        try:
            return envelope["result"]
        except KeyError as exc:
            raise RuntimeError(exc) from exc
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


def test_mutation_unrewritten_input_result_is_runtimeerror_result():
    """Wall (1): schema-sample without result is RuntimeError: 'result'."""
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


def test_naive_assignment_rewrite_is_syntaxerror_cannot_assign():
    """Wall (2): #350 rewrite of name['result'] = is SyntaxError."""
    ast.parse(_STORE_QUEUE_ASSIGN)
    naive = _naive_rewrite(_STORE_QUEUE_ASSIGN)
    assert '.get("result"' in naive
    try:
        ast.parse(naive)
    except SyntaxError as exc:
        assert "cannot assign to function call" in str(exc)
        assert "cannot assign to function call" in PRODUCT_ASSIGN_TO_CALL_HALT
    else:
        raise AssertionError("naive assignment rewrite must photograph SyntaxError")


def test_whole_module_fail_closed_photographs_result_miss():
    """#352 whole-module keep-original leaves envelope['result'] KeyError."""
    raw = _STORE_WORKFLOW_MIXED_FOR_STORE
    ast.parse(raw)
    naive = _naive_rewrite(raw)
    try:
        ast.parse(naive)
    except SyntaxError as exc:
        assert "cannot assign to function call" in str(exc)
    else:
        raise AssertionError("for-loop Store-ctx rewrite must be SyntaxError")
    # Whole-module fail-closed (the #352 path) would ship `raw` unchanged.
    ns: dict = {}
    exec(raw, ns, ns)
    try:
        ns["run"](input={"result": {"reference": "sample"}})
    except RuntimeError as exc:
        assert str(exc) == "'result'"
    else:
        raise AssertionError("unrewritten envelope['result'] must raise")


def test_emit_rewrites_reads_and_keeps_for_store_assignment():
    """Per-match fail-closed: skip for-target, rewrite envelope read."""
    rewritten = emit_result_key_access(_STORE_WORKFLOW_MIXED_FOR_STORE)
    ast.parse(rewritten)
    assert 'out["result"]' in rewritten or "out['result']" in rewritten
    assert 'envelope.get("result", envelope)' in rewritten
    assert "cannot assign to function call" not in rewritten
    ns: dict = {}
    exec(rewritten, ns, ns)
    out = ns["run"](input={"result": {"reference": "sample"}})
    assert out.get("status") == "ok"


def test_prepare_attaches_result_on_schema_sample():
    """keep-path / prepare_block_input still attach input['result']."""
    sample = _schema_sample()
    assert "result" not in sample
    prepared = prepare_block_input(
        "workflow", sample, roster=["workflow", "event_bus"]
    )
    assert prepared.get("result") not in (None, "")


def test_keep_path_persists_when_workflow_requires_input_result(tmp_path):
    """Unrewritten input['result'] still persists because prepare attaches."""
    sample = _schema_sample()
    assert "result" not in sample
    _emit_scheduling(tmp_path)
    _plant_block(tmp_path, "workflow", _STORE_WORKFLOW_INPUT_RESULT)
    _plant_block(tmp_path, "event_bus", _EVENT_BUS_NO_RESULT)
    body = _run_handle(tmp_path, sample)
    result = body["result"]
    assert result.get("ok") is not False, result
    assert body["rows"], "accept-payload persisted nothing"
    assert PRODUCT_WORKFLOW_RESULT_HALT not in str(result)
    assert "RuntimeError" not in str(result.get("error") or "")


def test_keep_path_persists_after_mixed_for_store_cloner_rewrite(tmp_path):
    """CLONER rewrite of mixed Store workflow: persist, no SyntaxError."""
    sample = _schema_sample()
    _emit_scheduling(tmp_path)
    shipped = _prepare_cloned_python(_STORE_WORKFLOW_MIXED_FOR_STORE)
    ast.parse(shipped)
    _plant_block(tmp_path, "workflow", shipped)
    _plant_block(tmp_path, "event_bus", _EVENT_BUS_NO_RESULT)
    _plant_block(tmp_path, "queue", emit_result_key_access(_STORE_QUEUE_ASSIGN))
    body = _run_handle(tmp_path, sample)
    result = body["result"]
    assert result.get("ok") is not False, result
    assert body["rows"], "accept-payload persisted nothing"
    assert PRODUCT_ASSIGN_TO_CALL_HALT not in str(result)
    assert "SyntaxError" not in str(result.get("error") or "")
    assert PRODUCT_WORKFLOW_RESULT_HALT not in str(result)
