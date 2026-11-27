"""F-8a: the Layer-1 function grader is generic and its gates bite.

Every workspace here declares its product contract in ``app/models.py``
(``grader_contract``): the grader's two inputs are two VALID instances of
that declared spec, never words of the capability description.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import app.factory.build.grader as _grader_module
import app.factory.build.product_grader.function_grader as fg
from tests.factory.grader_contract import COMPUTES, write_models

READS_INPUT = COMPUTES
CONSTANT = "def handle(payload):\n    return {'ok': True}\n"


def _workspace(tmp_path: Path, bodies: dict, declared=None) -> Path:
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    for cid, body in bodies.items():
        (actions / f"{cid}.py").write_text(body)
    write_models(tmp_path, list(bodies), declared)
    return tmp_path


def _bp(ids):
    return {"capabilities": [{"id": i, "description": f"record the {i} entry with owner and amount"} for i in ids]}


def _grade(ids, ws, **kw):
    """The grade of the plan capabilities ``ids`` (the plan TESTER iterates)."""
    return fg.grade_functions(_bp(ids), ws, capability_ids=ids, **kw)


def _classes(grade, ids):
    record = {"layer1": {"functions": grade.to_dict()}, "layer2": {"ran": True}, "ui": {"ran": True}}
    return {r["capability"]: r["class"] for r in _grader_module.reason_classes(record, ids) if r["layer"] == "function"}


BLUEPRINTS = [
    ["intake_form", "invoice_total", "visit_log", "tenant_notice"],
    ["crew_roster", "defect_raise", "permit_check", "photo_upload", "daily_rollup"],
]


@pytest.mark.parametrize("ids", BLUEPRINTS)
def test_two_blueprints_two_graded_sets(tmp_path, ids):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in ids})
    grade = _grade(ids, ws)
    assert [f.capability_id for f in grade.functions] == ids
    assert grade.ok, grade.to_dict()
    assert all(set(f.mutants_killed) == set(fg.MUTANTS) for f in grade.functions)
    assert all(set(f.depends_on) == {"owner", "amount", "approved"} for f in grade.functions)


@pytest.mark.parametrize("bad", ["invoice_total", "visit_log"])
def test_constant_function_is_named_and_fails_as_stub(tmp_path, bad):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: (CONSTANT if i == bad else READS_INPUT) for i in ids})
    grade = _grade(ids, ws)
    assert not grade.ok
    assert grade.failing == [bad]
    (r,) = [f for f in grade.functions if f.capability_id == bad]
    assert r.detail == fg.ONE_RESULT and r.mutants_survived == list(fg.MUTANTS)
    assert _classes(grade, ids) == {bad: "stub_handler"}


@pytest.mark.parametrize("n", [1, 3])
def test_too_few_functions_fail(tmp_path, n):
    ids = BLUEPRINTS[1][:n]
    ws = _workspace(tmp_path, {i: READS_INPUT for i in ids})
    grade = _grade(ids, ws)
    assert not grade.ok and any("too few" in f for f in grade.failing)


# --- the two inputs are two valid instances of the declared spec ----------

SPECS = [
    {"fields": [
        {"name": "kind", "type": "str", "allowed_values": ["alpha", "beta"]},
        {"name": "count", "type": "int", "min": 0, "max": 5},
        {"name": "flag", "type": "bool"},
        {"name": "contact", "type": "email"},
        {"name": "on", "type": "date"},
        {"name": "note", "type": "str"},
    ]},
    {"fields": [
        {"name": "rate", "type": "float", "min": 0, "max": 1},
        {"name": "at", "type": "datetime"},
        {"name": "slot", "type": "str", "format": "time"},
        {"name": "ref", "type": "uuid"},
        {"name": "size", "type": "int", "min": 3, "max": 3},
        {"name": "share", "type": "float", "min": 0.2, "max": 0.8},
    ]},
]


@pytest.mark.parametrize("spec", SPECS)
def test_contract_inputs_are_two_valid_instances(spec):
    from app.factory.build.roles_handlers import _sample_payload

    first, second = fg.contract_inputs(spec)
    assert first == _sample_payload(spec)
    assert set(first) == set(second) == {f["name"] for f in spec["fields"]}
    for f in spec["fields"]:
        name = f["name"]
        pinned = f.get("min") is not None and f.get("min") == f.get("max")
        assert (first[name] == second[name]) is pinned, name
        if f.get("allowed_values"):
            assert second[name] in f["allowed_values"]
        if f.get("min") is not None:
            assert f["min"] <= second[name] <= f["max"]
        assert type(first[name]) is type(second[name])


@pytest.mark.parametrize("spec", [
    None, {}, {"fields": []}, {"fields": [{"type": "int"}]},
    {"fields": [{"name": "only", "type": "str", "allowed_values": ["one"]}]},
    {"fields": [{"name": "pinned", "type": "int", "min": 2, "max": 2}]},
])
def test_no_variable_contract_gives_one_input(spec):
    from app.factory.build.roles_handlers import _sample_payload

    assert fg.contract_inputs(spec) == [_sample_payload(dict(spec or {}))]


# --- echo, noise and validation ------------------------------------------

ECHO_PLUS_ID = [
    "import uuid\ndef handle(payload):\n    return {'ok': True, 'record': dict(payload), 'id': uuid.uuid4().hex}\n",
    "import time\ndef handle(payload):\n"
    "    return {'ok': True, 'echo': sorted(payload.items()), 'at': time.time_ns()}\n",
]


@pytest.mark.parametrize("body", ECHO_PLUS_ID)
def test_echo_plus_fresh_id_is_a_stub(tmp_path, body):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: body for i in ids})
    grade = _grade(ids, ws)
    assert not grade.ok
    assert {f.detail for f in grade.functions} == {fg.ONE_RESULT}
    assert all(f.depends_on == [] for f in grade.functions)
    assert set(_classes(grade, ids).values()) == {"stub_handler"}


TIMESTAMPED = [
    "import datetime\ndef handle(payload):\n"
    "    return {'ok': True, 'total': payload['amount'] * 7, 'at': datetime.datetime.now().isoformat()}\n",
    "import time, uuid\ndef handle(payload):\n"
    "    return {'ok': True, 'meta': {'id': str(uuid.uuid4()), 'ns': time.time_ns()},\n"
    "            'result': [payload['owner'].upper(), not payload['approved']]}\n",
]


@pytest.mark.parametrize("body", TIMESTAMPED)
def test_answer_with_a_timestamp_is_not_failed_for_it(tmp_path, body):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: body for i in ids})
    grade = _grade(ids, ws)
    assert grade.ok, grade.to_dict()


VALIDATING = [
    # refuses any payload that lacks a declared field (TESTER's sample also
    # carries the record envelope, so an exact-set check would refuse it)
    "def handle(payload):\n"
    "    if not {'owner', 'amount', 'approved'} <= set(payload):\n"
    "        return {'ok': False, 'error': 'payload does not match the declared fields'}\n"
    "    return {'ok': True, 'due': payload['amount'] * 11 if payload['approved'] else 0}\n",
    # refuses a missing or mistyped declared field
    "def handle(payload):\n"
    "    amount = payload.get('amount')\n"
    "    if not isinstance(amount, int) or not isinstance(payload.get('approved'), bool):\n"
    "        return {'ok': False, 'error': 'amount and approved are required'}\n"
    "    return {'ok': True, 'band': 'high' if amount > 1 else 'low'}\n",
]


@pytest.mark.parametrize("body", VALIDATING)
def test_validating_handle_that_accepts_the_declared_sample_passes(tmp_path, body):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: body for i in ids})
    grade = _grade(ids, ws)
    assert grade.ok, grade.to_dict()
    # the same handle refuses a payload of undeclared keys
    scope: dict = {}
    exec(body, scope)
    assert scope["handle"]({"record": "x", "the": 1, "text": "y"})["ok"] is False


@pytest.mark.parametrize("body,fields", [
    ("def handle(payload):\n    return {'ok': True, 'n': payload['amount'] + 40}\n", ["amount"]),
    ("def handle(payload):\n    return {'ok': True, 'go': 'yes' if payload['approved'] else 'no',"
     " 'who': len(payload['owner'])}\n", ["owner", "approved"]),
])
def test_field_dependence_is_reported(tmp_path, body, fields):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: body for i in ids})
    grade = _grade(ids, ws)
    assert grade.ok, grade.to_dict()
    assert all(f.depends_on == fields for f in grade.functions)


# --- no field can vary: TESTER's one-input pilot rule, never ungraded -----

BARE_OK = "def handle(payload):\n    return {'ok': True, 'count': len(payload) + 3}\n"


@pytest.mark.parametrize("declared", [[], ["intake_form", "invoice_total"]])
def test_capability_with_no_declared_fields_is_judged_by_one_input(tmp_path, declared):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: (READS_INPUT if i in declared else BARE_OK) for i in ids}, declared=declared)
    grade = _grade(ids, ws)
    bare = [i for i in ids if i not in declared]
    assert [f.capability_id for f in grade.functions] == ids and grade.ungraded == []
    assert {f.detail for f in grade.functions if f.capability_id in bare} == {fg.ONE_INPUT}
    assert all(f.one_input_passed for f in grade.functions if f.capability_id in bare)
    assert grade.ok, grade.to_dict()
    assert "ungraded" not in _classes(grade, ids).values()


@pytest.mark.parametrize("models", ["", "MODELS = {}\n"])
def test_no_models_at_all_is_one_input_each(tmp_path, models):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: BARE_OK for i in ids})
    (ws / "app" / "models.py").write_text(models)
    grade = _grade(ids, ws)
    assert [f.inputs for f in fg.derive_functions(ids, ws)] == [[{}]] * len(ids)
    assert grade.ok and {f.detail for f in grade.functions} == {fg.ONE_INPUT}


@pytest.mark.parametrize("body,quoted", [
    ("def handle(payload):\n    return {'ok': False, 'error': 'nothing to do'}\n",
     "bare_cap rejected a payload built from its own schema: nothing to do"),
    ("def handle(payload):\n    return ['not', 'a', 'dict']\n",
     "bare_cap returned a non-dict: ['not', 'a', 'dict']"),
    ("def handle(payload):\n    return {'ok': True, 'steps': [{'status': 'failed'}]}\n",
     'bare_cap reported ok around a failed block call: {"ok": true, "steps": [{"status": "failed"}]}'),
    (BARE_OK, ""),
])
def test_one_input_fails_only_by_testers_rule_quoting_its_string(tmp_path, body, quoted):
    ws = _workspace(tmp_path, {"bare_cap": body}, declared=[])
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(["bare_cap"], ws)]
    assert r.ok is (not quoted) and r.one_input_passed is (not quoted)
    assert r.detail == (quoted or fg.ONE_INPUT)
    assert r.mutants_survived == [] and r.mutants_killed == []
    if quoted:
        record = {"layer1": {"functions": fg.FunctionGrade([r]).to_dict()}, "layer2": {"ran": True}}
        classes = {x["capability"]: x["class"] for x in _grader_module.reason_classes(record, ["bare_cap"])}
        assert classes["bare_cap"] == "handler_error"


# --- the handback carries the exact input sent and the answer observed ----

@pytest.mark.parametrize("body,failing", [(CONSTANT, True), (READS_INPUT, False)])
def test_probe_artifact_on_a_failing_handle_only(tmp_path, body, failing):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: body for i in ids})
    grade = _grade(ids, ws)
    for f in grade.functions:
        if failing:
            assert f.probe["inputs"] == fg.derive_functions(ids, ws)[0].inputs
            assert [json.loads(a) for a in f.probe["answers"]] == [{"ok": True}, {"ok": True}]
        else:
            assert f.probe == {}


# --- mutants on the grader itself -------------------------------------------

def test_grader_mutant_skip_a_function_reddens(tmp_path, monkeypatch):
    ids = BLUEPRINTS[1]
    ws = _workspace(tmp_path, {i: READS_INPUT for i in ids})
    real = fg.derive_functions
    monkeypatch.setattr(fg, "derive_functions", lambda ids, w, **kw: real(ids, w, **kw)[1:])
    grade = _grade(ids, ws)
    assert [f.capability_id for f in grade.functions] != ids


@pytest.mark.parametrize("survivor", [["field_dependence"], ["field_dependence", "other"]])
def test_grader_mutant_accept_survivor_reddens(survivor):
    r = fg.FunctionResult("x", True, [], survivor)
    assert not r.ok
    assert not fg.FunctionGrade([r] * 5).ok


# --- G2 findings F8-F10, F14, F17-F19, F22 --------------------------------
import subprocess as _sp

RAISES = [
    "def handle(payload):\n    raise ValueError('always')\n",
    "def handle(payload):\n    raise RuntimeError(str(payload))\n",
]


@pytest.mark.parametrize("body", RAISES)
def test_handle_that_always_raises_is_not_ok(tmp_path, body):  # F22
    ws = _workspace(tmp_path, {"raise_cap": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(["raise_cap"], ws)]
    assert not r.ok and not r.two_input_passed and r.mutants_killed == []


@pytest.mark.parametrize("body", [
    "import no_such_module_xyz\ndef handle(payload):\n    return payload\n",
    "def handle(payload):\n    return payload\nraise ImportError('boom')\n",
])
def test_action_module_that_fails_to_import_fails(tmp_path, body):  # F19
    ws = _workspace(tmp_path, {"bad_import": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(["bad_import"], ws)]
    assert not r.ok and "import failed" in r.detail


@pytest.mark.parametrize("cid", ["slow_a", "slow_b"])
def test_timeout_is_a_fail_with_every_mutant_unkilled(tmp_path, monkeypatch, cid):  # F18
    ws = _workspace(tmp_path, {cid: READS_INPUT})
    (fn,) = fg.derive_functions([cid], ws)

    def hang(*a, **k):
        raise _sp.TimeoutExpired(cmd="python", timeout=k.get("timeout"))

    monkeypatch.setattr(fg.subprocess, "run", hang)
    r = fg.run_function(fn, ws)
    assert not r.ok and r.detail == "timeout" and r.mutants_survived == list(fg.MUTANTS)


@pytest.mark.parametrize("name,member", [("field_dependence", True), ("constant_return", False), ("drop_rule", False)])
def test_mutant_set_is_field_dependence(name, member):  # F17: the tautological pair is gone
    assert set(fg.MUTANTS) == {"field_dependence"} and (name in fg.MUTANTS) is member


@pytest.mark.parametrize("body", [
    "def handle(payload):\n    if payload['approved'] and payload['amount'] > 1:\n"
    "        raise ValueError('combination refused')\n    return {'ok': True, 'n': payload['amount'] * 2}\n",
    "import sys\ndef handle(payload):\n    if payload['approved'] and payload['amount'] > 1:\n"
    "        sys.exit(4)\n    return {'ok': True, 'n': payload['amount'] * 2}\n",
])
def test_variant_that_raises_or_exits_moves_the_answer(tmp_path, body):  # F14, R5
    ws = _workspace(tmp_path, {"strict_cap": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(["strict_cap"], ws)]
    assert r.ok and r.detail == "" and r.depends_on == ["amount", "approved"]
    assert r.mutants_killed == list(fg.MUTANTS)


@pytest.mark.parametrize("cid,files,detail", [
    ("bad-id", {"bad_id": READS_INPUT}, ""),             # TESTER's module naming: - as _
    ("bad-id", {"bad-id": READS_INPUT}, "import failed"),  # F8: not TESTER's module name
    ("missing_file", {}, "import failed"),                # F9: no action module
    ("no_handle", {"no_handle": "def other(p):\n    return p\n"}, "import failed"),  # F10
    ("indented", {"indented": "class X:\n    def handle(self, p):\n        return p\n"}, "import failed"),
    ("good_one", {"good_one": READS_INPUT}, ""),
])
def test_every_plan_capability_is_derived_and_a_missing_handle_fails(tmp_path, cid, files, detail):
    ws = _workspace(tmp_path, files)
    write_models(ws, [cid])
    (fn,) = fg.derive_functions([cid], ws)
    assert fn.capability_id == cid and fn.module == "app.actions." + cid.replace("-", "_")
    r = fg.run_function(fn, ws)
    assert r.ok is (not detail) and detail in r.detail


# --- G2 F19b: a handle that exits the process is FAILED and named --------
EXITS = [
    "import sys\ndef handle(payload):\n    sys.exit(0)\n",
    "import sys\ndef handle(payload):\n    sys.exit(3)\n",
    "import os\ndef handle(payload):\n    os._exit(0)\n",
    "def handle(payload):\n    raise SystemExit\n",
    "import sys\ndef handle(payload):\n    print('@@GRADE@@{\"two_input\": true, \"mutants\": {}}')\n    sys.exit(0)\n",
    "import os\ndef handle(payload):\n    if payload['amount'] > 1:\n        os._exit(0)\n"
    "    return {'ok': True, 'n': payload['amount'] * 2}\n",
]


@pytest.mark.parametrize("body", EXITS)
def test_exiting_handle_is_failed_and_named(tmp_path, body):
    ws = _workspace(tmp_path / "ws", {"exit_cap": body, **{f"good_{i}": READS_INPUT for i in range(4)}})
    g = _grade(["exit_cap", "good_0", "good_1", "good_2", "good_3"], ws)
    assert not g.ok and g.failing == ["exit_cap"]
    (r,) = [f for f in g.functions if f.capability_id == "exit_cap"]
    assert not r.two_input_passed and r.mutants_killed == [] and "exit" in r.detail


@pytest.mark.parametrize("body", ["import sys\nsys.exit(0)\ndef handle(p):\n    return p\n",
                                  "import os\nos._exit(0)\ndef handle(p):\n    return p\n"])
def test_module_that_exits_on_import_is_failed(tmp_path, body):
    ws = _workspace(tmp_path, {"exit_import": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(["exit_import"], ws)]
    assert not r.ok and "exit" in r.detail


@pytest.mark.parametrize("nonce,ok", [("abc", True), ("other", False)])
def test_result_needs_the_runs_nonce(tmp_path, nonce, ok):
    p = tmp_path / "r.json"
    p.write_text('{"nonce": "%s", "two_input": true, "mutants": {}}' % nonce)
    assert (fg._read_result(p, "abc") is not None) is ok


# --- G2 #80: F20 min_functions enforced, F21 to_dict failing, R5, R1 -------
def _ok_result(cid):
    return fg.FunctionResult(cid, True, list(fg.MUTANTS), [], "")


@pytest.mark.parametrize("asked,n,ok", [(0, 1, False), (-5, 3, False), (1, 1, False), (2, 3, False),
                                       (4, 4, True), (6, 5, False), (6, 6, True)])
def test_min_functions_is_a_floor(asked, n, ok):  # F20
    g = fg.FunctionGrade([_ok_result(f"c{i}") for i in range(n)], min_functions=asked)
    assert g.min_functions == max(asked, fg.MIN_FUNCTIONS) and g.ok is ok
    assert any("too few" in f for f in g.failing) is (not ok)


@pytest.mark.parametrize("asked", [True, "0", 2.5, None])
def test_min_functions_must_be_an_int(asked):  # F20
    with pytest.raises(TypeError):
        fg.FunctionGrade([], min_functions=asked)


@pytest.mark.parametrize("asked", [0, 1])
def test_grade_functions_cannot_lower_the_floor(tmp_path, asked):  # F20
    ids = BLUEPRINTS[1][:2]
    g = _grade(ids, _workspace(tmp_path, {i: READS_INPUT for i in ids}), min_functions=asked)
    assert not g.ok and g.to_dict()["min_functions"] == fg.MIN_FUNCTIONS


@pytest.mark.parametrize("bad", [["c1"], ["c0", "c3"]])
def test_to_dict_failing_path(bad):  # F21
    m = list(fg.MUTANTS)
    rs = [fg.FunctionResult(f"c{i}", f"c{i}" not in bad, [] if f"c{i}" in bad else m,
                            m if f"c{i}" in bad else [], "x") for i in range(5)]
    d = fg.FunctionGrade(rs, errors=["derivation error"]).to_dict()
    assert d["ok"] is False and d["graded"] == 5 and d["min_functions"] == fg.MIN_FUNCTIONS
    assert d["failing"] == bad + ["derivation error"]
    assert [f["capability_id"] for f in d["functions"] if f["ok"] is False] == bad
    assert all(f["ok"] is True for f in d["functions"] if f["capability_id"] not in bad)


@pytest.mark.parametrize("n", [2, 3])
def test_to_dict_too_few_path(n):  # F21
    d = fg.FunctionGrade([_ok_result(f"c{i}") for i in range(n)]).to_dict()
    assert d["ok"] is False and d["failing"] == [f"too few gradeable functions: {n} < {fg.MIN_FUNCTIONS}"]


@pytest.mark.parametrize("n_ungraded,ok", [(0, True), (1, False), (2, False)])
def test_ungraded_keeps_the_grade_red_but_out_of_failing(n_ungraded, ok):
    gaps = [fg.FunctionResult(f"u{i}", False, [], [], "the grader did not run") for i in range(n_ungraded)]
    g = fg.FunctionGrade([_ok_result(f"c{i}") for i in range(4)], ungraded=gaps)
    assert g.ok is ok and g.failing == []
    assert [u["capability_id"] for u in g.to_dict()["ungraded"]] == [f"u{i}" for i in range(n_ungraded)]


@pytest.mark.parametrize("raise_,word", [
    ("raise ValueError('v')", "handle raised: ValueError"),
    ("raise KeyError('k')", "handle raised: KeyError"),
    ("raise KeyboardInterrupt", "handle raised: KeyboardInterrupt"),
    ("raise GeneratorExit", "handle raised: GeneratorExit"),
    ("class Odd(BaseException):\n        pass\n    raise Odd()", "handle raised: Odd"),
    ("raise SystemExit(2)", "handle exited: SystemExit"),
])
def test_base_exception_vs_exception(tmp_path, raise_, word):  # R5
    ws = _workspace(tmp_path, {"cap": f"def handle(payload):\n    {raise_}\n"})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(["cap"], ws)]
    assert not r.ok and r.detail.startswith(word) and r.mutants_killed == []


@pytest.mark.parametrize("rc,ok", [(0, True), (1, False), (137, False), (-9, False)])
def test_nonzero_returncode_fails_even_with_a_result(tmp_path, monkeypatch, rc, ok):  # R1
    ws = _workspace(tmp_path, {"cap": READS_INPUT})
    (fn,) = fg.derive_functions(["cap"], ws)

    def fake(cmd, *, input, **kw):
        spec = json.loads(input)
        Path(spec["out"]).write_text(json.dumps({"nonce": spec["nonce"], "two_input": True,
                                                 "mutants": {m: "killed" for m in spec["mutants"]}}))
        return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")

    monkeypatch.setattr(fg.subprocess, "run", fake)
    r = fg.run_function(fn, ws)
    assert r.ok is ok
    assert (f"rc={rc}" in r.detail) is (not ok)


# --- G2 5809bb8 exact gaps: F20 a raised floor is honoured ----------------
@pytest.mark.parametrize("asked,n,ok", [(5, 5, True), (6, 5, False), (5, 4, False)])
def test_grade_functions_honours_a_raised_min(tmp_path, asked, n, ok):  # F20
    ids = BLUEPRINTS[1][:n]
    g = _grade(ids, _workspace(tmp_path, {i: READS_INPUT for i in ids}), min_functions=asked)
    assert g.ok is ok and g.to_dict()["min_functions"] == asked


# --- no capability is dropped by its id or its module's shape: TESTER drops none ---
@pytest.mark.parametrize("cid,files", [
    ("caf\u00e9", {"caf\u00e9": READS_INPUT}),                  # non-ASCII identifier
    ("handler_only", {"handler_only": "def handler(p):\n    return p\n"}),
    ("in_string", {"in_string": 'X = """\ndef handle(p):\n"""\n'}),
    ("broken_src", {"broken_src": "def handle(p:\n"}),
])
def test_derivation_drops_no_plan_capability(tmp_path, cid, files):
    ws = _workspace(tmp_path, files)
    assert [f.capability_id for f in fg.derive_functions([cid, "class_"], ws)] == [cid, "class_"]


# --- G2 P8: 'graded' is the number of graded functions, not a constant ---
@pytest.mark.parametrize("n", [0, 3, 4, 7])
def test_to_dict_graded_counts_the_functions(n):
    rs = [fg.FunctionResult(f"c{i}", True, list(fg.MUTANTS), [], "x") for i in range(n)]
    assert fg.FunctionGrade(rs).to_dict()["graded"] == n


# --- G2 N7: a handle that is not a function answering a result is graded and FAILS ---
@pytest.mark.parametrize("cid,body", [
    ("class_handle", "class handle:\n    def __call__(self, p):\n        return p\n"),
    ("assigned_handle", "def _h(p):\n    return p\nhandle = _h\n"),
    ("async_handle", "async def handle(p):\n    return p\n"),
])
def test_non_function_handle_is_graded_and_fails(tmp_path, cid, body):
    ws = _workspace(tmp_path, {cid: body, "real_one": READS_INPUT})
    rs = {r.capability_id: r.ok for r in (fg.run_function(f, ws) for f in fg.derive_functions([cid, "real_one"], ws))}
    assert rs == {cid: False, "real_one": True}


# --- Fix 4: a handle whose right answer to made-up input is a refusal is
# judged on a state the grade makes with the plan's other capabilities ---
KEEPER = (
    "import json, os\n"
    "from pathlib import Path\n"
    "def handle(payload):\n"
    "    rows = Path(os.environ['STORAGE_PATH']) / 'rows.jsonl'\n"
    "    with rows.open('a') as fh:\n"
    "        fh.write(json.dumps(payload) + '\\n')\n"
    "    return {'ok': True, 'kept': payload['amount'] * 7 + len(payload['owner'])}\n"
)


def _checker(match: str, error: str = "no such entry") -> str:
    """A handle that answers only for a stored row; ``match`` is the Python
    test a stored row ``r`` must pass against the input ``p``."""
    return (
        "import json, os\n"
        "from pathlib import Path\n"
        "def handle(p):\n"
        "    rows = Path(os.environ['STORAGE_PATH']) / 'rows.jsonl'\n"
        "    seen = [json.loads(x) for x in rows.read_text().splitlines()] if rows.exists() else []\n"
        f"    if any({match} for r in seen):\n"
        "        return {'ok': True, 'checked': p['amount'] * 2}\n"
        f"    return {{'ok': False, 'error': {error!r}}}\n"
    )


def _judge(tmp_path, ids, bodies):
    ws = _workspace(tmp_path, bodies)
    return {r.capability_id: r for r in (fg.run_function(f, ws) for f in fg.derive_functions(ids, ws))}


@pytest.mark.parametrize("match,depends", [
    ("r['owner'] == p['owner']", ["owner"]),
    ("r['amount'] == p['amount'] and r['approved'] == p['approved']", ["amount", "approved"]),
])
def test_refusing_handle_passes_on_a_state_the_grade_made(tmp_path, match, depends):
    rs = _judge(tmp_path, ["checker", "keeper"], {"checker": _checker(match), "keeper": KEEPER})
    r = rs["checker"]
    assert r.ok and r.two_input_passed and r.detail == ""
    assert sorted(r.depends_on) == depends and r.mutants_killed == list(fg.MUTANTS)
    assert rs["keeper"].ok


@pytest.mark.parametrize("error", ["entry is locked", "not yours"])
def test_refused_although_stored_is_a_fail_quoting_what_the_run_saw(tmp_path, error):
    rs = _judge(tmp_path, ["checker", "keeper"], {"checker": _checker("False", error), "keeper": KEEPER})
    r = rs["checker"]
    assert not r.ok and r.detail == fg.REFUSED_ALTHOUGH_STORED and not r.needs_fixture
    assert any(error in a for a in r.probe["answers"])
    grade = fg.FunctionGrade([r, rs["keeper"]])
    assert _classes(grade, ["checker", "keeper"])["checker"] == "handler_error"


@pytest.mark.parametrize("match", ["True", "len(r) > 0"])
def test_accepting_every_change_after_the_fixture_is_a_stub(tmp_path, match):
    rs = _judge(tmp_path, ["checker", "keeper"], {"checker": _checker(match), "keeper": KEEPER})
    r = rs["checker"]
    assert not r.ok and r.detail == fg.ACCEPTS_ANY_CHANGE and r.mutants_survived == list(fg.MUTANTS)
    assert _classes(fg.FunctionGrade([r, rs["keeper"]]), ["checker", "keeper"])["checker"] == "stub_handler"


@pytest.mark.parametrize("error", ["no such entry", "unknown owner"])
def test_no_capability_makes_the_state_is_ungraded_not_billed(tmp_path, error):
    ws = _workspace(tmp_path, {"checker": _checker("True", error)})
    grade = _grade(["checker"], ws)
    assert [f.capability_id for f in grade.ungraded] == ["checker"] and grade.functions == []
    assert grade.ungraded[0].detail == fg.NO_FIXTURE and not grade.ok
    assert any(error in a for a in grade.ungraded[0].probe["answers"])
    assert "checker" not in grade.failing
    assert _classes(grade, ["checker"])["checker"] == "ungraded"


@pytest.mark.parametrize("other", [
    "def handle(p):\n    return {'ok': False, 'error': 'store offline'}\n",
    "def handle(p):\n    raise RuntimeError('down')\n",
])
def test_no_other_capability_accepted_stays_block_unavailable(tmp_path, other):
    r = _judge(tmp_path, ["checker", "other"], {"checker": _checker("True"), "other": other})["checker"]
    assert not r.ok and r.detail == fg.BLOCK_UNAVAILABLE and not r.needs_fixture


@pytest.mark.parametrize("cid,body", [("computes", READS_INPUT), ("constant", CONSTANT)])
def test_fixture_never_runs_for_a_handle_that_does_not_refuse(tmp_path, cid, body):
    # The keeper would make a state; a handle that answers anyway is judged as before.
    r = _judge(tmp_path, [cid, "keeper"], {cid: body, "keeper": KEEPER})[cid]
    assert r.detail == ("" if cid == "computes" else fg.ONE_RESULT)
    assert r.ok is (cid == "computes")


def _fn(cid, *firsts):
    return fg.GradedFunction(cid, f"app.actions.{cid}", [dict(f) for f in firsts])


@pytest.mark.parametrize("mine,theirs,covered", [
    ({"a": 1}, {"a": 2, "b": 3}, True),
    ({"a": 1, "b": 2}, {"a": 2, "b": 3}, True),
    ({"a": 1, "c": 2}, {"a": 2, "b": 3}, False),
    ({}, {"a": 2}, False),
])
def test_fixture_sources_are_the_capabilities_that_take_every_field(mine, theirs, covered):
    fn, other = _fn("mine", mine), _fn("theirs", theirs)
    got = fg.fixture_sources(fn, [fn, other])
    assert got == ([{"capability": "theirs", "module": "app.actions.theirs", "sample": theirs}] if covered else [])


@pytest.mark.parametrize("first", [{"a": 1}, {"a": 1, "b": 2}])
def test_fixture_sources_never_list_the_capability_itself_or_an_empty_one(first):
    fn = _fn("mine", first)
    assert fg.fixture_sources(fn, [fn, fg.GradedFunction("empty", "m", [])]) == []
    assert fg.fixture_sources(fg.GradedFunction("none", "m", []), [fn]) == []


_FIX = [{"capability": "theirs", "module": "m", "sample": {}}]


@pytest.mark.parametrize("data", [
    {"honest_error": False, "fixture": {"stored_by": ["theirs"], "accepted": False}},
    {"honest_error": True, "error": "handle raised", "fixture": {"stored_by": ["theirs"]}},
    {"honest_error": "yes", "fixture": {"stored_by": ["theirs"], "accepted": False}},
    {"honest_error": True, "fixture": {"stored_by": []}},
    {"honest_error": True},
])
def test_fixture_result_does_not_fire(data):
    fn = fg.GradedFunction("mine", "m", [{"a": 1}], list(_FIX))
    assert fg.fixture_result(fn, data) is None


@pytest.mark.parametrize("fixture,ok,detail,depends", [
    ({"stored_by": ["theirs"], "accepted": True, "refused_when_changed": ["a"]}, True, "", ["a"]),
    ({"stored_by": ["theirs"], "accepted": True, "refused_when_changed": ["a", "b"]}, True, "", ["a", "b"]),
    ({"stored_by": ["theirs"], "accepted": True, "refused_when_changed": []}, False, fg.ACCEPTS_ANY_CHANGE, []),
    ({"stored_by": ["theirs"], "accepted": False}, False, fg.REFUSED_ALTHOUGH_STORED, []),
    ({"stored_by": ["theirs"], "accepted": "yes"}, False, fg.REFUSED_ALTHOUGH_STORED, []),
    ({"stored_by": ["theirs"], "accepted": True, "error": "handle raised: X"}, False, "handle raised: X", []),
])
def test_fixture_result_reads_the_facts(fixture, ok, detail, depends):
    fn = fg.GradedFunction("mine", "m", [{"a": 1}], list(_FIX))
    r = fg.fixture_result(fn, {"honest_error": True, "answers": ["A1", "A2"],
                               "fixture": dict(fixture, answers=["F1"])})
    assert (r.ok, r.detail, r.depends_on, r.needs_fixture) == (ok, detail, depends, False)
    assert r.two_input_passed is (detail in ("", fg.ACCEPTS_ANY_CHANGE))
    assert r.mutants_killed == (list(fg.MUTANTS) if ok else [])
    assert r.probe == ({} if ok else {"inputs": [{"a": 1}], "corrections": [], "answers": ["A1", "A2", "F1"]})


@pytest.mark.parametrize("answers", [["A1", "A2"], ["B1", "B2"]])
def test_fixture_result_without_a_source_needs_a_fixture(answers):
    fn = fg.GradedFunction("mine", "m", [{"a": 1}])
    r = fg.fixture_result(fn, {"honest_error": True, "answers": answers})
    assert (r.ok, r.needs_fixture, r.detail, r.probe["answers"]) == (False, True, fg.NO_FIXTURE, answers)


def _wide_models(root: Path) -> None:
    """``checker`` declares ``Entry``; ``keeper`` declares the same fields
    under other bounds, so its own sample differs from the checker's input."""
    from tests.factory.grader_contract import MODEL_CLASS

    wide = (MODEL_CLASS.replace("class Entry", "class Wide").replace("'entry'", "'wide'")
            .replace("'min': 0, 'max': 1000", "'min': 500, 'max': 900"))
    (root / "app" / "models.py").write_text(
        MODEL_CLASS + "\n" + wide + "\nMODELS = {'checker': Entry, 'keeper': Wide}\n")


@pytest.mark.parametrize("match", [
    "r['amount'] == p['amount']",
    "r['amount'] == p['amount'] and r['owner'] == p['owner']",
])
def test_the_fixture_stores_input_one_not_its_own_sample(tmp_path, match):
    ws = _workspace(tmp_path, {"checker": _checker(match), "keeper": KEEPER})
    _wide_models(ws)
    fns = {f.capability_id: f for f in fg.derive_functions(["checker", "keeper"], ws)}
    assert fns["keeper"].inputs[0]["amount"] != fns["checker"].inputs[0]["amount"]
    r = fg.run_function(fns["checker"], ws)
    assert r.ok and r.detail == "" and "amount" in r.depends_on


# --- Fix 4, second round: the state counts as made only when the store,
# read back, holds input one; each source is tried alone on a fresh store ---
ERASER = (
    "import os\n"
    "from pathlib import Path\n"
    "def handle(payload):\n"
    "    rows = Path(os.environ['STORAGE_PATH']) / 'rows.jsonl'\n"
    "    rows.write_text('')\n"
    "    return {'ok': True, 'removed': payload['amount'] * 3 + 1}\n"
)
RAISER = "def handle(payload):\n    raise RuntimeError('source down')\n"
SQL_KEEPER = (
    "import os, sqlite3\n"
    "from pathlib import Path\n"
    "def handle(p):\n"
    "    con = sqlite3.connect(str(Path(os.environ['STORAGE_PATH']) / 'platform.db'))\n"
    "    con.execute('create table if not exists entry (owner text, amount integer, approved integer)')\n"
    "    con.execute('insert into entry values (?, ?, ?)', (p['owner'], p['amount'], p['approved']))\n"
    "    con.commit(); con.close()\n"
    "    return {'ok': True, 'kept': p['amount'] + 11}\n"
)


def _pure(k: int) -> str:
    """A source that accepts input one and stores nothing."""
    return f"def handle(p):\n    return {{'ok': True, 'sum': p['amount'] * {k} + 2}}\n"


def _sql_checker(match: str) -> str:
    return (
        "import os, sqlite3\n"
        "from pathlib import Path\n"
        "def handle(p):\n"
        "    db = Path(os.environ['STORAGE_PATH']) / 'platform.db'\n"
        "    rows = []\n"
        "    if db.exists():\n"
        "        con = sqlite3.connect(str(db))\n"
        "        try:\n"
        "            rows = [dict(zip(('owner', 'amount', 'approved'), r)) for r in con.execute('select * from entry')]\n"
        "        except sqlite3.Error:\n"
        "            rows = []\n"
        "        con.close()\n"
        f"    if any({match} for r in rows):\n"
        "        return {'ok': True, 'checked': p['amount'] * 2}\n"
        "    return {'ok': False, 'error': 'no such entry'}\n"
    )


@pytest.mark.parametrize("match,depends", [
    ("r['owner'] == p['owner']", ["owner"]),
    ("r['amount'] == p['amount']", ["amount"]),
])
@pytest.mark.parametrize("order", [
    ["checker", "keeper", "eraser"],
    ["checker", "eraser", "keeper"],
    ["eraser", "keeper", "checker"],
])
def test_a_source_that_removes_the_state_changes_nothing_in_any_plan_order(tmp_path, match, depends, order):
    rs = _judge(tmp_path, order, {"checker": _checker(match), "keeper": KEEPER, "eraser": ERASER})
    r = rs["checker"]
    assert (r.ok, r.detail, sorted(r.depends_on), r.needs_fixture) == (True, "", depends, False)


@pytest.mark.parametrize("k", [5, 9])
def test_an_ok_answer_that_stores_nothing_is_ungraded_not_billed(tmp_path, k):
    ws = _workspace(tmp_path, {"checker": _checker("r['owner'] == p['owner']"), "pure": _pure(k)})
    grade = _grade(["checker", "pure"], ws)
    (u,) = [f for f in grade.ungraded if f.capability_id == "checker"]
    assert (u.detail, u.needs_fixture) == (fg.NOT_STORED, True)
    assert "checker" not in grade.failing and not grade.ok
    assert _classes(grade, ["checker", "pure"])["checker"] == "ungraded"


@pytest.mark.parametrize("order", [["checker", "eraser"], ["eraser", "checker"]])
def test_a_source_that_only_removes_is_ungraded(tmp_path, order):
    r = _judge(tmp_path, order, {"checker": _checker("True"), "eraser": ERASER})["checker"]
    assert (r.ok, r.detail, r.needs_fixture) == (False, fg.NOT_STORED, True)


@pytest.mark.parametrize("match,depends", [
    ("r['owner'] == p['owner']", ["owner"]),
    ("r['amount'] == p['amount']", ["amount"]),
])
@pytest.mark.parametrize("order", [["raiser", "keeper", "checker"], ["keeper", "raiser", "checker"]])
def test_a_raising_source_does_not_stop_the_next_one(tmp_path, match, depends, order):
    r = _judge(tmp_path, order, {"checker": _checker(match), "keeper": KEEPER, "raiser": RAISER})["checker"]
    assert (r.ok, r.detail, sorted(r.depends_on)) == (True, "", depends)


@pytest.mark.parametrize("match,depends", [
    ("r['owner'] == p['owner']", ["owner"]),
    ("r['amount'] == p['amount'] and r['owner'] == p['owner']", ["amount", "owner"]),
])
def test_a_state_read_back_from_a_sqlite_store_is_judged(tmp_path, match, depends):
    r = _judge(tmp_path, ["checker", "keeper"], {"checker": _sql_checker(match), "keeper": SQL_KEEPER})["checker"]
    assert (r.ok, r.detail, sorted(r.depends_on)) == (True, "", depends)


@pytest.mark.parametrize("n", [1, 3])
def test_refused_although_stored_quotes_the_answer_seen_after_the_fixture(tmp_path, n):
    # The refusal names the rows it saw, so the answer after the fixture
    # differs from the two before it; only the probe can quote it.
    body = (
        "import os\n"
        "from pathlib import Path\n"
        "def handle(p):\n"
        "    rows = Path(os.environ['STORAGE_PATH']) / 'rows.jsonl'\n"
        "    seen = rows.read_text().splitlines() if rows.exists() else []\n"
        f"    return {{'ok': False, 'error': 'locked after ' + str(len(seen) * {n}) + ' rows'}}\n"
    )
    r = _judge(tmp_path, ["checker", "keeper"], {"checker": body, "keeper": KEEPER})["checker"]
    assert r.detail == fg.REFUSED_ALTHOUGH_STORED
    assert any(f"locked after {n} rows" in a for a in r.probe["answers"])
    # Input one was refused on the store, so no changed field was sent:
    # the two answers before the fixture, then the one after it.
    assert len(r.probe["answers"]) == 3


@pytest.mark.parametrize("record,payload,shown", [
    ([1, "ann", 1], {"approved": True, "owner": "ann", "amount": 1.0}, True),
    (["bob", 7, 0], {"owner": "bob", "amount": 7, "approved": False}, True),
    (["bob", 7], {"owner": "bob", "amount": 8}, False),
    (["bob", 7], {"owner": "bob", "tags": ["x"]}, True),
    (["bob", 7], {"tags": ["x"], "meta": {}}, False),
    (["bob", 7], {}, False),
    ([None, 2.5], {"note": None, "rate": 2.5}, True),
    ([None, 2.5], {"note": None, "rate": 2.25}, False),
])
def test_shows_input_compares_values_as_stored(record, payload, shown):
    assert fg.shows_input(record, payload) is shown


@pytest.mark.parametrize("owner,amount", [("ann", 3), ("bob", 41)])
def test_stored_records_reads_back_sqlite_rows_json_lines_and_json_cells(tmp_path, owner, amount):
    import sqlite3

    con = sqlite3.connect(str(tmp_path / "a.db"))
    con.execute("create table t (owner text, amount integer, doc text)")
    con.execute("insert into t values (?, ?, ?)", (owner, amount, json.dumps({"inner": owner + "!"})))
    con.commit()
    con.close()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "rows.jsonl").write_text(json.dumps({"o": owner, "n": {"a": amount}}) + "\nnot json\n")
    (tmp_path / "plain.txt").write_text(f"{owner} {amount}")
    got = fg.stored_records(tmp_path)
    assert [owner, amount, json.dumps({"inner": owner + "!"})] in got
    assert [owner + "!"] in got and [owner, amount] in got and [amount] in got
    assert not any(f"{owner} {amount}" in r for r in got)


def test_stored_records_of_an_empty_or_unreadable_store_is_empty(tmp_path):
    assert fg.stored_records(tmp_path) == []
    (tmp_path / "broken.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)
    assert fg.stored_records(tmp_path) == []


_T_KEEP = {"source": "keep", "stored": True, "source_accepted": True, "accepted": True,
           "refused_when_changed": ["owner"], "answers": ["K1", "K2"]}
_T_PURE = {"source": "pure", "stored": False, "source_accepted": True, "accepted": False, "answers": ["P1"]}
_T_DENY = {"source": "deny", "stored": True, "source_accepted": True, "accepted": False, "answers": ["D1"]}
_T_ERR = {"source": "err", "stored": True, "source_accepted": False, "error": "handle raised: X", "answers": []}
_T_MEM = {"source": "mem", "stored": False, "source_accepted": True, "accepted": True,
          "refused_when_changed": ["amount"], "answers": ["M1"]}


@pytest.mark.parametrize("trials,want", [
    ([_T_KEEP, _T_PURE], {"accepted_by": ["keep", "pure"], "stored_by": ["keep"], "took_by": ["keep"],
                          "answers": ["K1", "K2"], "accepted": True, "refused_when_changed": ["owner"]}),
    ([_T_DENY, _T_PURE], {"accepted_by": ["deny", "pure"], "stored_by": ["deny"], "took_by": [],
                          "answers": ["D1"], "accepted": False, "refused_when_changed": []}),
    ([_T_DENY, _T_KEEP], {"accepted_by": ["deny", "keep"], "stored_by": ["deny", "keep"], "took_by": ["keep"],
                          "answers": ["D1", "K1", "K2"], "accepted": True, "refused_when_changed": ["owner"]}),
    ([_T_ERR, _T_DENY], {"accepted_by": ["deny"], "stored_by": ["deny", "err"], "took_by": [], "answers": ["D1"],
                         "accepted": False, "refused_when_changed": [], "error": "handle raised: X"}),
    ([_T_ERR, _T_KEEP], {"accepted_by": ["keep"], "stored_by": ["err", "keep"], "took_by": ["keep"],
                         "answers": ["K1", "K2"], "accepted": True, "refused_when_changed": ["owner"]}),
    ([_T_MEM, _T_KEEP], {"accepted_by": ["keep", "mem"], "stored_by": ["keep"], "took_by": ["keep", "mem"],
                         "answers": ["K1", "K2", "M1"], "accepted": True, "refused_when_changed": ["amount", "owner"]}),
    ([_T_PURE], {"accepted_by": ["pure"], "stored_by": [], "took_by": [], "answers": [],
                 "accepted": False, "refused_when_changed": []}),
    ([], {"accepted_by": [], "stored_by": [], "took_by": [], "answers": [], "accepted": False,
          "refused_when_changed": []}),
])
def test_combine_trials_is_the_same_in_any_order(trials, want):
    assert fg.combine_trials(trials) == want
    assert fg.combine_trials(list(reversed(trials))) == want


@pytest.mark.parametrize("fixture", [
    {"stored_by": [], "accepted_by": ["pure"], "accepted": False},
    {"stored_by": [], "accepted_by": ["pure", "eraser"]},
])
def test_fixture_result_accepted_but_not_stored_is_ungraded(fixture):
    fn = fg.GradedFunction("mine", "m", [{"a": 1}], list(_FIX))
    r = fg.fixture_result(fn, {"honest_error": True, "answers": ["A1", "A2"], "fixture": fixture})
    assert (r.ok, r.detail, r.needs_fixture, r.probe["answers"]) == (False, fg.NOT_STORED, True, ["A1", "A2"])


@pytest.mark.parametrize("error", ["entry is locked", "not yours"])
def test_a_store_holding_part_of_input_one_never_bills_a_refusal(tmp_path, error):
    # SQL_KEEPER stores three of input one's fields, not the envelope's:
    # the read-back cannot show input one, so the refusal is not the writer's.
    checker = _sql_checker("False").replace("'no such entry'", repr(error))
    r = _judge(tmp_path, ["checker", "keeper"], {"checker": checker, "keeper": SQL_KEEPER})["checker"]
    assert (r.ok, r.detail, r.needs_fixture) == (False, fg.NOT_STORED, True)
    assert any(error in a for a in r.probe["answers"])


@pytest.mark.parametrize("field,depends", [("owner", ["owner"]), ("amount", ["amount"])])
def test_a_state_kept_in_memory_is_judged_when_the_handle_takes_it(tmp_path, field, depends):
    # The source keeps the state in its own module; no store shows it, but
    # the handle's own answer changes from a refusal to an answer.
    memory = "SEEN = []\ndef handle(p):\n    SEEN.append(dict(p))\n    return {'ok': True, 'n': len(SEEN) * 7}\n"
    checker = (
        "from app.actions import memory\n"
        "def handle(p):\n"
        f"    if any(r[{field!r}] == p[{field!r}] for r in memory.SEEN):\n"
        "        return {'ok': True, 'checked': p['amount'] * 2}\n"
        "    return {'ok': False, 'error': 'no such entry'}\n"
    )
    r = _judge(tmp_path, ["checker", "memory"], {"checker": checker, "memory": memory})["checker"]
    assert (r.ok, r.detail, sorted(r.depends_on)) == (True, "", depends)


@pytest.mark.parametrize("refused", [["a"], ["a", "b"]])
def test_fixture_result_judges_a_taken_input_even_with_no_read_back(refused):
    fn = fg.GradedFunction("mine", "m", [{"a": 1}], list(_FIX))
    fixture = {"stored_by": [], "accepted_by": ["mem"], "accepted": True, "refused_when_changed": refused}
    r = fg.fixture_result(fn, {"honest_error": True, "answers": ["A1"], "fixture": fixture})
    assert (r.ok, r.detail, r.depends_on) == (True, "", refused)


@pytest.mark.parametrize("seed_file", ["rows.jsonl", "seed/rows.json"])
def test_a_record_in_the_store_before_the_source_ran_is_not_the_sources(tmp_path, seed_file):
    # The product's own migration seeds a record holding input one; the
    # source stores nothing. Only a record the SOURCE added counts, so the
    # refusal stays ungraded instead of billed to the writer.
    ws = _workspace(tmp_path, {"checker": _checker("False"), "pure": _pure(4)})
    first = {f.capability_id: f for f in fg.derive_functions(["checker", "pure"], ws)}["checker"].inputs[0]
    (ws / "app" / "migrations.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "def upgrade_head():\n"
        f"    path = Path(os.environ['STORAGE_PATH']) / {seed_file!r}\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"    path.write_text(json.dumps({first!r}) + '\\n')\n"
    )
    r = {x.capability_id: x for x in (fg.run_function(f, ws) for f in fg.derive_functions(["checker", "pure"], ws))}
    assert (r["checker"].detail, r["checker"].needs_fixture) == (fg.NOT_STORED, True)


@pytest.mark.parametrize("code", [0, 3])
@pytest.mark.parametrize("order", [["exiter", "keeper", "checker"], ["keeper", "exiter", "checker"]])
def test_a_source_that_ends_its_process_does_not_stop_the_next_one(tmp_path, code, order):
    exiter = f"import os\ndef handle(payload):\n    os._exit({code})\n"
    r = _judge(tmp_path, order, {"checker": _checker("r['owner'] == p['owner']"), "keeper": KEEPER, "exiter": exiter})
    assert (r["checker"].ok, r["checker"].detail, r["checker"].depends_on) == (True, "", ["owner"])
