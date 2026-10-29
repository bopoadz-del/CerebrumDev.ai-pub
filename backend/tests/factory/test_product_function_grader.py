"""F-8a: the Layer-1 function grader is generic and its gates bite."""

from __future__ import annotations

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
