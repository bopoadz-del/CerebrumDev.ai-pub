"""F-8c-3: GRADE.md renders every part of the grade, line by line."""

from __future__ import annotations

import pytest

from app.factory.build.product_grader import grade_md as gm


def _grade(certified=True, l1_ok=True, ran=True, fns=(), steps=(), failing=(), verdicts=(), open_fails=(), tasks=()):
    return {
        "product_id": "lettings-desk", "certified": certified,
        "layer1": {"ok": l1_ok, "functions": {"graded": len(fns), "min_functions": 4, "functions": list(fns)},
                   "ui": {"steps": list(steps), "failing": list(failing)}},
        "layer2": {"ran": ran, "verdicts": list(verdicts), "open_fails": list(open_fails)},
        "next_tasks": list(tasks),
    }


def _lines(g):
    return gm.render_md(g).splitlines()


@pytest.mark.parametrize("certified,want", [(True, "YES"), (False, "NO"), ("true", "NO")])
def test_certified_line(certified, want):  # G28
    assert _lines(_grade(certified=certified))[2] == f"**Certified:** {want} (Layer 1 decides; Layer 2 can only block)"


@pytest.mark.parametrize("ok,want", [(True, "GREEN"), (False, "RED"), (1, "RED")])
def test_layer1_header(ok, want):  # G29
    assert f"## Layer 1 (deterministic): {want}" in _lines(_grade(l1_ok=ok))


@pytest.mark.parametrize("f,want", [
    ({"capability_id": "rent_roll", "ok": True, "mutants_killed": ["constant_return", "drop_rule"], "mutants_survived": []},
     "  - `rent_roll`: PASS; mutants killed constant_return, drop_rule; survived none"),
    ({"capability_id": "notice_log", "ok": False, "mutants_killed": [], "mutants_survived": ["drop_rule"]},
     "  - `notice_log`: FAIL; mutants killed none; survived drop_rule"),
])
def test_function_lines(f, want):  # G30
    lines = _lines(_grade(fns=[f]))
    assert "- Functions graded: 1 (min 4)" in lines and want in lines


@pytest.mark.parametrize("steps,failing,count", [
    ([{"passed": True}, {"passed": True}], [], "2/2"),
    ([{"passed": True}, {"passed": False}, {"passed": "yes"}], ["route:/v1/x", "screen:map"], "1/3"),
])
def test_ui_lines(steps, failing, count):  # G31
    lines = _lines(_grade(steps=steps, failing=failing))
    assert f"- UI steps passed: {count}" in lines
    assert [line for line in lines if line.startswith("  - FAIL: ")] == [f"  - FAIL: {n}" for n in failing]


@pytest.mark.parametrize("ran,verdicts,open_fails,tasks", [
    (True, [{"verdict": "PASS", "question": "q1", "evidence": "e1"}], [], []),
    (False, [], ["layer 2 did not answer: TimeoutError"], ["re-run writer on: layer 2 did not answer: TimeoutError"]),
    (True, [{"verdict": "FAIL", "question": "q2", "evidence": None}], ["q2"], ["re-run writer on: q2"]),
])
def test_layer2_and_next_tasks(ran, verdicts, open_fails, tasks):  # G32
    lines = _lines(_grade(ran=ran, verdicts=verdicts, open_fails=open_fails, tasks=tasks))
    assert f"## Layer 2 (separate grader context): {'ran' if ran else 'did not run'}" in lines
    for v in verdicts:
        assert f"- {v['verdict']}: {v['question']} -- evidence: \"{v['evidence'] or ''}\"" in lines
    assert [line for line in lines if line.startswith("- OPEN: ")] == [f"- OPEN: {q}" for q in open_fails]
    if tasks:
        i = lines.index("## Next tasks")
        assert lines[i + 1:] == [f"- {t}" for t in tasks]
    else:
        assert "## Next tasks" not in lines


@pytest.mark.parametrize("pid", ["lettings-desk", "site-ops"])
def test_write_grade_md(tmp_path, pid):
    g = dict(_grade(), product_id=pid)
    path = gm.write_grade_md(tmp_path / "out", g)
    assert path.name == gm.GRADE_MD and path.read_text("utf-8") == gm.render_md(g)
    assert path.read_text("utf-8").splitlines()[0] == f"# GRADE -- {pid}"


# --- G2 #77: ok / ran / passed are `is True`, so a 'yes' never shows PASS --
TRUTHY = ["yes", 1, "true", ["x"]]


@pytest.mark.parametrize("ok", TRUTHY)
def test_truthy_function_ok_shows_fail(ok):
    lines = _lines(_grade(fns=[{"capability_id": "c", "ok": ok}]))
    assert "  - `c`: FAIL; mutants killed none; survived none" in lines


@pytest.mark.parametrize("ran", TRUTHY + [False])
def test_truthy_ran_shows_did_not_run(ran):
    assert "## Layer 2 (separate grader context): did not run" in _lines(_grade(ran=ran))


@pytest.mark.parametrize("ok", TRUTHY)
def test_truthy_layer1_ok_shows_red(ok):
    assert "## Layer 1 (deterministic): RED" in _lines(_grade(l1_ok=ok))


@pytest.mark.parametrize("passed", TRUTHY)
def test_truthy_step_not_counted(passed):
    assert "- UI steps passed: 1/2" in _lines(_grade(steps=[{"passed": True}, {"passed": passed}]))


@pytest.mark.parametrize("v,shown", [
    ({"verdict": "PASS", "question": "q", "evidence": "quote"}, "PASS"),
    ({"verdict": "yes", "question": "q", "evidence": "quote"}, "FAIL (recorded 'yes')"),
    ({"verdict": "pass", "question": "q", "evidence": "quote"}, "FAIL (recorded 'pass')"),
    ({"verdict": "PASS", "question": "q", "evidence": "  "}, "FAIL (recorded 'PASS')"),
    ({"verdict": "PASS", "question": "q", "evidence": 5}, "FAIL (recorded 'PASS')"),
    ({"verdict": True, "question": "q", "evidence": "quote"}, "FAIL (recorded True)"),
])
def test_verdict_shows_pass_only_when_it_is_one(v, shown):
    assert any(line.startswith(f"- {shown}: q -- evidence:") for line in _lines(_grade(verdicts=[v])))
