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
