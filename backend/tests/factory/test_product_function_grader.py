"""F-8a: the Layer-1 function grader is generic and its gates bite."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.factory.build.product_grader import function_grader as fg

READS_INPUT = (
    "def handle(payload):\n"
    "    return {'ok': True, 'echo': sorted(payload.items())}\n"
)
CONSTANT = "def handle(payload):\n    return {'ok': True}\n"


def _workspace(tmp_path: Path, bodies: dict) -> Path:
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    for cid, body in bodies.items():
        (actions / f"{cid}.py").write_text(body)
    return tmp_path


def _bp(ids):
    return {"capabilities": [{"id": i, "description": f"record the {i} entry with owner and amount"} for i in ids]}


BLUEPRINTS = [
    ["intake_form", "invoice_total", "visit_log", "tenant_notice"],
    ["crew_roster", "defect_raise", "permit_check", "photo_upload", "daily_rollup"],
]


@pytest.mark.parametrize("ids", BLUEPRINTS)
def test_two_blueprints_two_graded_sets(tmp_path, ids):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in ids})
    grade = fg.grade_functions(_bp(ids), ws)
    assert [f.capability_id for f in grade.functions] == ids
    assert grade.ok, grade.to_dict()
    assert all(set(f.mutants_killed) == set(fg.MUTANTS) for f in grade.functions)


@pytest.mark.parametrize("bad", ["invoice_total", "visit_log"])
def test_constant_function_is_named_and_fails(tmp_path, bad):
    ids = BLUEPRINTS[0]
    ws = _workspace(tmp_path, {i: (CONSTANT if i == bad else READS_INPUT) for i in ids})
    grade = fg.grade_functions(_bp(ids), ws)
    assert not grade.ok
    assert grade.failing == [bad]


@pytest.mark.parametrize("n", [1, 3])
def test_too_few_functions_fail(tmp_path, n):
    ids = BLUEPRINTS[1][:n]
    ws = _workspace(tmp_path, {i: READS_INPUT for i in ids})
    grade = fg.grade_functions(_bp(ids), ws)
    assert not grade.ok and any("too few" in f for f in grade.failing)


@pytest.mark.parametrize("cap", [{"id": "a_b", "description": "alpha beta gamma"}, {"id": "zz", "description": "owner amount date"}])
def test_two_inputs_differ_in_every_key(cap):
    a, b = fg.two_inputs(cap)
    assert set(a) == set(b) and all(a[k] != b[k] for k in a)


# --- mutants on the grader itself -------------------------------------------

def test_grader_mutant_skip_a_function_reddens(tmp_path, monkeypatch):
    ids = BLUEPRINTS[1]
    ws = _workspace(tmp_path, {i: READS_INPUT for i in ids})
    real = fg.derive_functions
    monkeypatch.setattr(fg, "derive_functions", lambda bp, w: real(bp, w)[1:])
    grade = fg.grade_functions(_bp(ids), ws)
    assert [f.capability_id for f in grade.functions] != ids


@pytest.mark.parametrize("survivor", [["constant_return"], ["drop_rule"]])
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
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp(["raise_cap"]), ws)]
    assert not r.ok and not r.two_input_passed and r.mutants_killed == []


@pytest.mark.parametrize("body", [
    "import no_such_module_xyz\ndef handle(payload):\n    return payload\n",
    "def handle(payload):\n    return payload\nraise ImportError('boom')\n",
])
def test_action_module_that_fails_to_import_fails(tmp_path, body):  # F19
    ws = _workspace(tmp_path, {"bad_import": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp(["bad_import"]), ws)]
    assert not r.ok and "import failed" in r.detail


@pytest.mark.parametrize("cid", ["slow_a", "slow_b"])
def test_timeout_is_a_fail_with_every_mutant_unkilled(tmp_path, monkeypatch, cid):  # F18
    ws = _workspace(tmp_path, {cid: READS_INPUT})

    def hang(*a, **k):
        raise _sp.TimeoutExpired(cmd="python", timeout=k.get("timeout"))

    monkeypatch.setattr(fg.subprocess, "run", hang)
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp([cid]), ws)]
    assert not r.ok and r.detail == "timeout" and r.mutants_survived == list(fg.MUTANTS)


@pytest.mark.parametrize("name", ["constant_return", "drop_rule"])
def test_mutant_set_is_the_standard_pair(name):  # F17
    assert set(fg.MUTANTS) == {"constant_return", "drop_rule"} and name in fg.MUTANTS


@pytest.mark.parametrize("body", [
    "def handle(payload):\n    if not payload:\n        raise KeyError('empty')\n    return sorted(payload.items())\n",
    "def handle(payload):\n    return [payload['quantity'], sorted(payload)]\n",
])
def test_mutant_that_raises_counts_as_killed(tmp_path, body):  # F14
    ws = _workspace(tmp_path, {"strict_cap": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp(["strict_cap"]), ws)]
    assert r.ok and "drop_rule" in r.mutants_killed


@pytest.mark.parametrize("cid,files,graded", [
    ("bad-id", {"bad-id": READS_INPUT}, False),           # F8: not an identifier
    ("9lives", {"9lives": READS_INPUT}, False),
    ("missing_file", {}, False),                         # F9: no action module
    ("no_handle", {"no_handle": "def other(p):\n    return p\n"}, False),  # F10
    ("indented", {"indented": "class X:\n    def handle(self, p):\n        return p\n"}, False),
    ("good_one", {"good_one": READS_INPUT}, True),
])
def test_derivation_filters(tmp_path, cid, files, graded):
    ws = _workspace(tmp_path, files)
    assert [f.capability_id for f in fg.derive_functions(_bp([cid]), ws)] == ([cid] if graded else [])


# --- G2 F19b: a handle that exits the process is FAILED and named --------
EXITS = [
    "import sys\ndef handle(payload):\n    sys.exit(0)\n",
    "import sys\ndef handle(payload):\n    sys.exit(3)\n",
    "import os\ndef handle(payload):\n    os._exit(0)\n",
    "def handle(payload):\n    raise SystemExit\n",
    "import sys\ndef handle(payload):\n    print('@@GRADE@@{\"two_input\": true, \"mutants\": {}}')\n    sys.exit(0)\n",
    "import os, sys\ndef handle(payload):\n    if payload.get('quantity', 0) > 10:\n        os._exit(0)\n    return sorted(payload.items())\n",
]


@pytest.mark.parametrize("body", EXITS)
def test_exiting_handle_is_failed_and_named(tmp_path, body):
    ws = _workspace(tmp_path / "ws", {"exit_cap": body, **{f"good_{i}": READS_INPUT for i in range(4)}})
    g = fg.grade_functions(_bp(["exit_cap", "good_0", "good_1", "good_2", "good_3"]), ws)
    assert not g.ok and g.failing == ["exit_cap"]
    (r,) = [f for f in g.functions if f.capability_id == "exit_cap"]
    assert not r.two_input_passed and r.mutants_killed == [] and "exit" in r.detail


@pytest.mark.parametrize("body", ["import sys\nsys.exit(0)\ndef handle(p):\n    return p\n",
                                  "import os\nos._exit(0)\ndef handle(p):\n    return p\n"])
def test_module_that_exits_on_import_is_failed(tmp_path, body):
    ws = _workspace(tmp_path, {"exit_import": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp(["exit_import"]), ws)]
    assert not r.ok and "exit" in r.detail


@pytest.mark.parametrize("nonce,ok", [("abc", True), ("other", False)])
def test_result_needs_the_runs_nonce(tmp_path, nonce, ok):
    p = tmp_path / "r.json"
    p.write_text('{"nonce": "%s", "two_input": true, "mutants": {}}' % nonce)
    assert (fg._read_result(p, "abc") is not None) is ok


# --- G2 #80: F20 min_functions enforced, F21 to_dict failing, R5, R1 -------
def _ok_result(cid):
    return fg.FunctionResult(cid, True, ["constant_return", "drop_rule"], [], "")


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
    g = fg.grade_functions(_bp(ids), _workspace(tmp_path, {i: READS_INPUT for i in ids}), min_functions=asked)
    assert not g.ok and g.to_dict()["min_functions"] == fg.MIN_FUNCTIONS


@pytest.mark.parametrize("bad", [["c1"], ["c0", "c3"]])
def test_to_dict_failing_path(bad):  # F21
    rs = [fg.FunctionResult(f"c{i}", f"c{i}" not in bad, [] if f"c{i}" in bad else ["drop_rule"],
                            ["drop_rule"] if f"c{i}" in bad else [], "x") for i in range(5)]
    d = fg.FunctionGrade(rs, errors=["derivation error"]).to_dict()
    assert d["ok"] is False and d["graded"] == 5 and d["min_functions"] == fg.MIN_FUNCTIONS
    assert d["failing"] == bad + ["derivation error"]
    assert [f["capability_id"] for f in d["functions"] if f["ok"] is False] == bad
    assert all(f["ok"] is True for f in d["functions"] if f["capability_id"] not in bad)


@pytest.mark.parametrize("n", [2, 3])
def test_to_dict_too_few_path(n):  # F21
    d = fg.FunctionGrade([_ok_result(f"c{i}") for i in range(n)]).to_dict()
    assert d["ok"] is False and d["failing"] == [f"too few gradeable functions: {n} < {fg.MIN_FUNCTIONS}"]


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
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp(["cap"]), ws)]
    assert not r.ok and r.detail.startswith(word) and r.mutants_killed == []


@pytest.mark.parametrize("rc,ok", [(0, True), (1, False), (137, False), (-9, False)])
def test_nonzero_returncode_fails_even_with_a_result(tmp_path, monkeypatch, rc, ok):  # R1
    ws = _workspace(tmp_path, {"cap": READS_INPUT})
    (fn,) = fg.derive_functions(_bp(["cap"]), ws)

    def fake(cmd, *, input, **kw):
        spec = json.loads(input)
        Path(spec["out"]).write_text(json.dumps({"nonce": spec["nonce"], "two_input": True,
                                                 "mutants": {m: "killed" for m in spec["mutants"]}}))
        return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")

    monkeypatch.setattr(fg.subprocess, "run", fake)
    r = fg.run_function(fn, ws)
    assert r.ok is ok
    assert (f"rc={rc}" in r.detail) is (not ok)


# --- G2 5809bb8 exact gaps: F20 a raised floor is honoured; R5 exit on the
# drop_rule input is a KILL of that mutant, not a grader exit --------------
@pytest.mark.parametrize("asked,n,ok", [(5, 5, True), (6, 5, False), (5, 4, False)])
def test_grade_functions_honours_a_raised_min(tmp_path, asked, n, ok):  # F20
    ids = BLUEPRINTS[1][:n]
    g = fg.grade_functions(_bp(ids), _workspace(tmp_path, {i: READS_INPUT for i in ids}), min_functions=asked)
    assert g.ok is ok and g.to_dict()["min_functions"] == asked


EXIT_ON_EMPTY = [
    "import sys\ndef handle(payload):\n    if not payload:\n        sys.exit(0)\n    return sorted(payload.items())\n",
    "def handle(payload):\n    if not payload:\n        raise SystemExit(4)\n    return sorted(payload.items())\n",
]


@pytest.mark.parametrize("body", EXIT_ON_EMPTY)
def test_exit_only_on_the_drop_rule_input_still_grades(tmp_path, body):  # R5
    ws = _workspace(tmp_path, {"cap": body})
    (r,) = [fg.run_function(f, ws) for f in fg.derive_functions(_bp(["cap"]), ws)]
    assert r.ok is True and r.detail == "" and "drop_rule" in r.mutants_killed


# --- hardwiring-gate repair: id and handle detection are derived (str.isidentifier / ast) ---
@pytest.mark.parametrize("cid,files,graded", [
    ("caf\u00e9", {"caf\u00e9": READS_INPUT}, False),                  # non-ASCII identifier
    ("class_", {"class_": READS_INPUT}, True),
    ("handler_only", {"handler_only": "def handler(p):\n    return p\n"}, False),
    ("in_string", {"in_string": 'X = """\ndef handle(p):\n"""\n'}, False),
    ("broken_src", {"broken_src": "def handle(p:\n"}, True),            # graded, fails on import
    ("after_other", {"after_other": "def other(p):\n    return p\n\ndef handle(p):\n    return p\n"}, True),
])
def test_derivation_is_structural(tmp_path, cid, files, graded):
    ws = _workspace(tmp_path, files)
    assert [f.capability_id for f in fg.derive_functions(_bp([cid]), ws)] == ([cid] if graded else [])
