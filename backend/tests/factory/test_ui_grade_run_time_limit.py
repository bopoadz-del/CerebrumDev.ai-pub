"""The UI grade's run has a time limit Playwright knows about, so a run that
runs out of time still writes its report. A step that limit stopped is not
run (ungraded), never a failure handed to the writer, and a step that ran and
failed before the limit is still a failure. Also pins three rules of the
not-run reading that no test held: which chain step blocks the rest, what a
mixed result list reads as, and that a not-run step alone keeps a grade from
passing."""

from __future__ import annotations

import pytest

import app.factory.build.grader as grader
import app.factory.build.product_grader.grade_md as gm
import app.factory.build.product_grader.ui_grader as ug

BP = {
    "product_id": "lettings-desk", "product_name": "Lettings Desk", "summary": "tenancy files",
    "ui_modules": ["tenant_portal"],
    "capabilities": [{"id": "rent_roll", "description": "Roll rent."}, {"id": "notice_log", "description": "Log notices."}],
}
LIMIT = ug.run_limit_ms(ug.RUN_TIMEOUT_S)


def _report(results, duration):
    """A Playwright JSON report in the shape a real run writes: each planned
    step with the result list named for it (one ``passed`` result when the
    step is not named), and the run's duration in ms."""
    return {
        "stats": {"duration": duration},
        "suites": [{"specs": [
            {"title": st.name, "tests": [{"results": [{"status": s} for s in results.get(st.name, ["passed"])]}]}
            for st in ug.ui_plan(BP)
        ]}],
    }


# --- the limit Playwright is given ------------------------------------------

@pytest.mark.parametrize("cap_s,limit_ms", [(600, 540_000), (120, 60_000), (30, 1_000)])
def test_the_run_limit_leaves_the_report_margin_below_the_cap(cap_s, limit_ms):
    assert ug.run_limit_ms(cap_s) == limit_ms
    assert ug.run_limit_ms(cap_s) < cap_s * 1000


@pytest.mark.parametrize("which", ["grade", "toolchain"])
def test_every_playwright_run_is_told_its_limit_below_its_cap(tmp_path, which):
    """The grade and the toolchain check both pass Playwright a global limit
    under the cap the process is killed at, so Playwright stops first and
    writes its report."""
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw["timeout"]
        raise OSError("stop here")

    if which == "grade":
        ug.grade_ui(BP, tmp_path, base_url="http://127.0.0.1:9", run=fake)
    else:
        ug.toolchain_check(run=fake, which=lambda *_a, **_k: "npx")
    flags = [a for a in seen["cmd"] if a.startswith("--global-timeout=")]
    assert flags == [f"--global-timeout={ug.run_limit_ms(seen['timeout'])}"]
    assert ug.run_limit_ms(seen["timeout"]) < seen["timeout"] * 1000
    assert seen["cmd"][:3] == ["npx", "playwright", "test"] and seen["cmd"][-1] == "--reporter=json"


@pytest.mark.parametrize("report,cut", [
    ({"stats": {"duration": LIMIT + 13.4}}, True),
    ({"stats": {"duration": LIMIT}}, True),
    ({"stats": {"duration": LIMIT - 1}}, False),
    ({"stats": {"duration": 3518.7}}, False),
    ({"stats": {"duration": str(LIMIT + 1)}}, False),
    ({"stats": None}, False),
    ({}, False),
    (None, False),
])
def test_a_run_that_lasted_its_whole_limit_was_cut(report, cut):
    assert ug.run_was_cut(report, LIMIT) is cut


# --- a cut run: what ran is graded, what did not run is not ----------------

#: What real Playwright records when its global limit stops a run: the step
#: running at the cut has one ``skipped`` result, the steps never started have
#: none.
AT_CUT = {"screen:tenant_portal": ["skipped"], "route:/v1/rent_roll": [], "route:/v1/notice_log": []}


@pytest.mark.parametrize("failed", ["load:/", "honest_error:/v1/rent_roll"])
def test_steps_the_limit_stopped_are_not_run_and_a_failure_before_it_stays(failed):
    g = ug.parse_report(_report({failed: ["failed"], **AT_CUT}, LIMIT + 13.4), BP)
    assert g.failing == [failed]
    assert [(r["name"], r["blocked_by"]) for r in g.not_run] == [(n, ug.TIME_LIMIT) for n in AT_CUT]
    assert not g.ok


@pytest.mark.parametrize("duration", [LIMIT - 1, 3518.7])
def test_without_the_limit_a_step_with_no_result_is_still_a_failure(duration):
    """The same report from a run that ended in time: nothing explains the
    missing results, so the steps did not pass."""
    g = ug.parse_report(_report({"load:/": ["failed"], **AT_CUT}, duration), BP)
    assert g.failing == ["load:/", *AT_CUT]
    assert g.not_run == []


@pytest.mark.parametrize("limit_ms,cut", [(5_000, True), (10_000, False)])
def test_the_limit_a_report_is_read_against_is_the_one_given(limit_ms, cut):
    g = ug.parse_report(_report(AT_CUT, 5_013.4), BP, limit_ms=limit_ms)
    assert [r["name"] for r in g.not_run] == (list(AT_CUT) if cut else [])
    assert g.failing == ([] if cut else list(AT_CUT))


@pytest.mark.parametrize("failed,after", [
    ("attach:pdf", ["attach:xlsx", "parsed:status", "answer:cited"]),
    ("parsed:status", ["answer:cited"]),
])
def test_in_a_cut_run_a_chain_step_is_still_blocked_by_its_failed_step(failed, after):
    """The chain step that failed is what stopped the steps after it, even in
    a run that also ran out of time."""
    results = {failed: ["failed"], **{n: [] for n in after}, **AT_CUT}
    g = ug.parse_report(_report(results, LIMIT + 1), BP)
    assert g.failing == [failed]
    blocked = {r["name"]: r["blocked_by"] for r in g.not_run}
    assert {n: blocked[n] for n in after} == {n: failed for n in after}
    assert {n: blocked[n] for n in AT_CUT} == {n: ug.TIME_LIMIT for n in AT_CUT}


@pytest.mark.parametrize("failed", ["load:/", "bind:tenant"])
def test_steps_the_limit_stopped_reach_the_grade_as_ungraded_not_as_writer_items(failed):
    ui = ug.parse_report(_report({failed: ["failed"], **AT_CUT}, LIMIT + 1), BP).to_dict()
    record = {"layer1": {"ui": ui}, "layer2": {"ran": True}}
    rows = [r for r in grader.reason_classes(record, []) if r["layer"] == "ui"]
    assert sorted(r["class"] for r in rows) == sorted(["failed_ui_step"] + ["ungraded"] * len(AT_CUT))
    md = gm.render_md({"product_id": "p", "layer1": {"ui": ui}, "layer2": {}})
    assert [line for line in md.splitlines() if line.startswith("  - NOT RUN: ")] == [
        f"  - NOT RUN: {n} (blocked by {ug.TIME_LIMIT})" for n in AT_CUT
    ]
    assert [line for line in md.splitlines() if line.startswith("  - FAIL: ")] == [f"  - FAIL: {failed}"]


# --- OPUSGRADE F299 faults 2-4: rules no test held --------------------------

@pytest.mark.parametrize("first,after", [
    ("attach:pdf", ["attach:xlsx", "parsed:status", "answer:cited"]),
    ("parsed:status", ["answer:cited"]),
])
def test_a_chain_step_skipped_with_nothing_failed_before_it_blocks_the_rest(first, after):
    """In a run that ended in time, the first chain step that did not pass
    -- even one Playwright skipped with no failure before it -- is the one
    the later skipped chain steps are blocked by. Nothing explains its own
    skip, so it is a failure."""
    results = {first: ["skipped"], **{n: ["skipped"] for n in after}}
    g = ug.parse_report(_report(results, 3518.7), BP)
    assert g.failing == [first]
    assert [(r["name"], r["blocked_by"]) for r in g.not_run] == [(n, first) for n in after]


@pytest.mark.parametrize("results,status", [
    (["failed", "skipped"], "failed"),
    (["skipped", "failed"], "failed"),
    (["timedOut", "skipped"], "failed"),
    (["skipped", "skipped"], "skipped"),
    (["skipped"], "skipped"),
    ([], "skipped"),
    (["passed", "skipped"], "failed"),
    (["passed"], "passed"),
])
def test_a_step_is_skipped_only_when_no_result_it_recorded_ran(results, status):
    report = {"suites": [{"specs": [{"title": "s", "tests": [{"results": [{"status": x} for x in results]}]}]}]}
    assert ug.report_statuses(report) == {"s": status}


@pytest.mark.parametrize("blocked_by", ["attach:pdf", ug.TIME_LIMIT])
def test_a_not_run_step_alone_keeps_the_grade_from_passing(blocked_by):
    steps = [
        {"name": "load:/", "kind": "load", "target": "/", "passed": True},
        {"name": "screen:x", "kind": "screen", "target": "/#/x", "passed": False,
         "status": ug.NOT_RUN, "blocked_by": blocked_by},
    ]
    g = ug.UiGrade(steps)
    assert g.failing == [] and g.ran
    assert g.ok is False
    assert ug.UiGrade(steps[:1]).ok is True

