"""A finding handed to the writer says what was checked and what came back.

A failed screen step carries what its kind expected and the failure text the
run recorded; a failed function carries why, what it was called with and what
it answered. A finding handed back word for word after the writer's last
round is marked unchanged, in the handback and in the log.

Every product id, capability id, step target and message below is invented.
"""

from __future__ import annotations

import pytest

import app.factory.build.grader as grader
import app.factory.build.grader_findings as gf
import app.factory.build.product_grader.function_grader as fg
import app.factory.build.product_grader.ui_grader as ug
from app.factory.build import runner as runner_mod
from tests.factory.test_a_grader import (  # noqa: F401
    IDS,
    READS_INPUT,
    WRITER_RUN,
    _bp,
    _fake_pass,
    _fake_pass_harness,
    _workspace,
    independent_grader,
    smoke_blueprint,
)

BPS = [
    {"product_id": "quillon-desk", "product_name": "Quillon Desk", "summary": "parcel files",
     "ui_modules": ["dock_board"], "capabilities": [{"id": "parcel_tally", "description": "Tally parcels."}]},
    {"product_id": "verrin_ops", "product_name": "Verrin Ops", "summary": "kiln logs",
     "ui_modules": ["kiln_wall", "map"], "capabilities": [{"id": "kiln_cycle", "description": "Log kiln cycles."}]},
]
NO_DOCS = {"ask": "Run the floor.", "documents": {"file_types": [], "uploads": False}}


def _report(results_by_title):
    """A Playwright JSON report: title -> list of result dicts."""
    return {"suites": [{"specs": [
        {"title": title, "tests": [{"results": results}]} for title, results in results_by_title.items()
    ]}]}


def _passed(names):
    return {n: [{"status": "passed"}] for n in names}


# --- report_failures --------------------------------------------------------

@pytest.mark.parametrize("message, want", [
    ("\x1b[31mError: expect(locator).toBeVisible() failed\x1b[39m\n\nLocator:  getByText(/zeta/i).first()",
     "Error: expect(locator).toBeVisible() failed Locator: getByText(/zeta/i).first()"),
    ("Expected: 200\nReceived: 404", "Expected: 200 Received: 404"),
])
def test_failure_text_is_the_runs_own_message_cleaned(message, want):
    report = _report({"bind:tenant": [{"status": "failed", "error": {"message": message}}]})
    assert ug.report_failures(report) == {"bind:tenant": want}


@pytest.mark.parametrize("field", ["error", "errors"])
def test_failure_text_is_read_from_error_or_errors(field):
    result = {"status": "failed"}
    result[field] = {"message": "boom one"} if field == "error" else [{"message": ""}, {"message": "boom two"}]
    got = ug.report_failures(_report({"route:/v1/a": [result]}))
    assert got == {"route:/v1/a": "boom one" if field == "error" else "boom two"}


@pytest.mark.parametrize("secret", ["tok-alpha-123", "tok-beta-987"])
def test_failure_text_redacts_the_bearer(secret):
    msg = f"GET /v1/x Authorization: Bearer {secret} -> 500"
    got = ug.report_failures(_report({"route:/v1/x": [{"status": "failed", "error": {"message": msg}}]}), secret)
    assert secret not in got["route:/v1/x"] and ug.REDACTED in got["route:/v1/x"]


@pytest.mark.parametrize("extra", [0, 1])
def test_failure_text_is_cut_at_the_boundary(extra):
    msg = "x" * (ug.FAILURE_CHARS + extra)
    got = ug.report_failures(_report({"s": [{"status": "failed", "error": {"message": msg}}]}))
    assert len(got["s"]) == ug.FAILURE_CHARS


@pytest.mark.parametrize("report", [
    _report(_passed(["load:/", "bind:tenant"])),
    _report({"bind:tenant": [{"status": "failed"}]}),          # failed, no message
    _report({"bind:tenant": [{"status": "failed", "error": {"message": "   "}}]}),
    [],
    "not a report",
])
def test_no_failure_text_where_none_was_recorded(report):
    assert ug.report_failures(report) == {}


@pytest.mark.parametrize("first, second", [("first", "second"), ("retry one", "retry two")])
def test_failure_text_first_failing_result_wins_and_nested_suites_are_read(first, second):
    report = {"suites": [{"suites": [{"specs": [{"title": "s", "tests": [{"results": [
        {"status": "passed"},
        {"status": "failed", "error": {"message": first}},
        {"status": "failed", "error": {"message": second}},
    ]}]}]}]}]}
    assert ug.report_failures(report) == {"s": first}


# --- parse_report rows ------------------------------------------------------

@pytest.mark.parametrize("bp", BPS)
def test_a_failed_step_row_carries_expected_and_saw(bp):
    names = [s.name for s in ug.ui_plan(bp, NO_DOCS)]
    statuses = _passed(names)
    statuses["bind:tenant"] = [{"status": "failed", "error": {"message": f"no text {bp['product_id']}"}}]
    grade = ug.parse_report(_report(statuses), bp, NO_DOCS)
    rows = {r["name"]: r for r in grade.steps}
    assert rows["bind:tenant"]["expected"] == f"the first page shows the text {bp['product_id']} " \
        "(case-insensitive; - and _ match any character)"
    assert rows["bind:tenant"]["saw"] == f"no text {bp['product_id']}"
    for name, row in rows.items():
        if name != "bind:tenant":
            assert "expected" not in row and "saw" not in row, name


@pytest.mark.parametrize("bp", BPS)
def test_a_failed_step_with_no_text_says_so(bp):
    names = [s.name for s in ug.ui_plan(bp, NO_DOCS)]
    route = next(n for n in names if n.startswith("route:"))
    statuses = _passed([n for n in names if n != route])   # route absent from the report
    grade = ug.parse_report(_report(statuses), bp, NO_DOCS)
    row = next(r for r in grade.steps if r["name"] == route)
    assert row["saw"] == ug.NO_FAILURE_TEXT
    assert row["expected"] == f"GET {row['target']} with the grading bearer answers 200"


@pytest.mark.parametrize("secret", ["sekret-one-aa", "sekret-two-bb"])
def test_parse_report_redacts_the_bearer_it_is_given(secret):
    bp = BPS[0]
    names = [s.name for s in ug.ui_plan(bp, NO_DOCS)]
    statuses = _passed(names)
    statuses["load:/"] = [{"status": "failed", "error": {"message": f"header {secret}"}}]
    rows = {r["name"]: r for r in ug.parse_report(_report(statuses), bp, NO_DOCS, secret=secret).steps}
    assert rows["load:/"]["saw"] == f"header {ug.REDACTED}"


@pytest.mark.parametrize("bp", BPS)
def test_a_not_run_chain_step_carries_no_evidence(bp):
    brief = {"ask": "Read files.", "documents": {"file_types": ["pdf"], "uploads": True}}
    names = [s.name for s in ug.ui_plan(bp, brief)]
    statuses = _passed(names)
    statuses["attach:pdf"] = [{"status": "failed", "error": {"message": "no + button"}}]
    statuses["parsed:status"] = [{"status": "skipped"}]
    rows = {r["name"]: r for r in ug.parse_report(_report(statuses), bp, brief).steps}
    assert rows["parsed:status"]["status"] == ug.NOT_RUN and "saw" not in rows["parsed:status"]
    assert rows["attach:pdf"]["saw"] == "no + button"


@pytest.mark.parametrize("target", ["/t", "/v1/other"])
def test_every_step_kind_has_an_expectation(target):
    kinds = set(ug.KIND_JS)
    assert kinds == set(ug.KIND_EXPECTS)
    assert all(ug.expectation(ug.UiStep("n", k, target)) for k in kinds)
    assert target in ug.expectation(ug.UiStep("n", "route", target))
    assert ug.expectation(ug.UiStep("n", "no_such_kind", target)) == ""


@pytest.mark.parametrize("row, want", [
    ({"expected": "A", "saw": "B"}, "expected: A; saw: B"),
    ({"expected": "C"}, "expected: C"),
    ({"saw": "D"}, "saw: D"),
    ({}, ""),
])
def test_step_evidence(row, want):
    assert ug.step_evidence(row) == want


# --- function evidence ------------------------------------------------------

@pytest.mark.parametrize("inputs, answers", [
    ([{"qty": 3}, {"qty": 4}], ["{'total': 9}", "{'total': 9}"]),
    ([{"rate": 0.5}, {"rate": 0.75}], ["1", "1"]),
])
def test_function_evidence_names_why_inputs_and_answers(inputs, answers):
    r = fg.FunctionResult("cap_x", False, [], ["field_dependence"], fg.ONE_RESULT,
                          probe={"inputs": inputs, "corrections": [], "answers": answers})
    ev = fg.function_evidence(r)
    assert ev.startswith(f"why: {fg.ONE_RESULT}; called with: ")
    assert fg._cut(inputs) in ev and fg._cut(answers) in ev


@pytest.mark.parametrize("passed_kind", ["two", "one"])
def test_a_survived_mutant_says_no_field_moved_the_answer(passed_kind):
    r = fg.FunctionResult("cap_y", passed_kind == "two", [], ["field_dependence"], "",
                          one_input_passed=passed_kind == "one")
    assert fg.function_evidence(r) == "why: no single input field it declares changed its answer"


@pytest.mark.parametrize("detail, want", [("", ""), ("rc=1", "why: rc=1"), ("rc=9", "why: rc=9")])
def test_function_evidence_leaves_out_parts_it_lacks(detail, want):
    assert fg.function_evidence(fg.FunctionResult("cap_z", False, [], [], detail)) == want


@pytest.mark.parametrize("n, cut", [(fg.EVIDENCE_CHARS, False), (fg.EVIDENCE_CHARS + 1, True)])
def test_evidence_cut_boundary(n, cut):
    got = fg._cut("y" * n)
    assert len(got) == fg.EVIDENCE_CHARS and got.endswith("...") is cut


@pytest.mark.parametrize("bad_id, detail", [("cap_bad", "rc=2"), ("cap_worse", "rc=3")])
def test_grade_evidence_is_for_failing_functions_only(bad_id, detail):
    good = fg.FunctionResult("cap_ok", True, ["field_dependence"], [], "")
    bad = fg.FunctionResult(bad_id, False, [], ["field_dependence"], detail)
    grade = fg.FunctionGrade([good, bad], min_functions=1)
    assert grade.evidence(bad_id) == f"why: {detail}"
    assert grade.evidence("cap_ok") == "" and grade.evidence("nope") == ""


# --- the item and its row ---------------------------------------------------

@pytest.mark.parametrize("item, evidence, rule, where", [
    ("UI step FAIL: bind:tenant", "expected: the first page shows the text qq; saw: none", "screen_walk", "bind:tenant"),
    ("UI step FAIL: route:/v1/kiln_cycle", "expected: GET /v1/kiln_cycle answers 200", "screen_walk",
     "route:/v1/kiln_cycle"),
    ("function grade FAIL: parcel_tally", "why: two inputs gave one result", "function_check", "parcel_tally"),
    ("function grade FAIL: kiln_cycle", "why: rc=1; answered: [\"x\"]", "function_check", "kiln_cycle"),
])
def test_the_row_locates_the_finding_by_name_not_by_its_evidence(item, evidence, rule, where):
    full = grader._with_evidence(item, evidence)
    assert full == f"{item}{grader.EVIDENCE_SEP}{evidence}"
    row = gf.finding_row(full, gf.WRITER, features={"parcel_tally", "kiln_cycle"})
    assert (row["rule"], row["where"]) == (rule, where)
    assert evidence.split(":")[0] in row["quote"]


@pytest.mark.parametrize("item", ["UI step FAIL: load:/", "function grade FAIL: kiln_cycle"])
def test_no_evidence_leaves_the_item_bare(item):
    assert grader._with_evidence(item, "") == item


@pytest.mark.parametrize("prefix", ["UI step FAIL: ", "function grade FAIL: "])
def test_the_findings_separator_is_the_graders(prefix):
    seps = {shape[0]: shape[3] for shape in gf.ITEM_SHAPES}
    assert seps[prefix] == grader.EVIDENCE_SEP


class _Fns:
    def __init__(self, text):
        self.text = text

    def evidence(self, name):
        return f"{self.text}:{name}"


@pytest.mark.parametrize("fns, want", [(_Fns("a"), "a:c1"), (_Fns("b"), "b:c1"), (object(), "")])
def test_fn_evidence_reads_the_grade_or_nothing(fns, want):
    assert grader._fn_evidence(fns, "c1") == want


# --- unchanged findings -----------------------------------------------------

@pytest.mark.parametrize("items, last, unchanged", [
    (["A", "B"], ["A"], ["A"]),
    (["C", "D"], ["C", "D"], ["C", "D"]),
    (["E"], [], []),
    (["F"], ["F -- other observation"], []),
    ([], ["G"], []),
])
def test_mark_unchanged(items, last, unchanged):
    handed, same = runner_mod.mark_unchanged(items, last)
    assert same == unchanged
    assert handed == [i + runner_mod.UNCHANGED_NOTE if i in unchanged else i for i in items]


def _run_rounds(monkeypatch, tmp_path, blueprint, verdicts):
    from app.factory.build.authority import BuildRole
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader.DISABLE_ENV, raising=False)
    seq = iter(verdicts)
    monkeypatch.setattr(grader, "run_grader", lambda **kw: next(seq))
    roles = _fake_pass_harness(monkeypatch)
    handed: list = []

    def writer(ctx):
        handed.append(tuple(str(i) for i in ctx.work_list))
        return _fake_pass(ctx)

    roles[BuildRole.WRITER] = writer
    r = RoleRunner(blueprint, tmp_path / "build", roles=roles,
                   brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}')
    r.run()
    return r, handed


def _nc(item):
    return grader.GraderVerdict(tier=grader.NOT_CERTIFIED, items=(item,), writer_items=(item,))


@pytest.mark.parametrize("item", [
    "UI step FAIL: bind:tenant -- expected: the first page shows the text zz; saw: not visible",
    "function grade FAIL: plinket_check -- why: two inputs gave one result",
])
def test_a_finding_repeated_word_for_word_is_handed_back_marked_and_logged(
    smoke_blueprint, tmp_path, monkeypatch, item  # noqa: F811
):
    from app.factory.build.ledger import EventKind

    r, handed = _run_rounds(monkeypatch, tmp_path, smoke_blueprint, [_nc(item)] * 6)
    reworks = [h for h in handed if h]
    assert reworks[0] == (item,)
    assert all(h == (item + runner_mod.UNCHANGED_NOTE,) for h in reworks[1:]) and len(reworks) >= 2
    events = [e for e in r.ledger.events() if e.kind is EventKind.REWORK
              and (e.payload or {}).get("gate") == grader.GRADER_GATE]
    assert all(e.payload["failure_names"] == [item] for e in events)
    notes = [e for e in r.ledger.events() if e.kind is EventKind.NOTE and (e.payload or {}).get("unchanged")]
    assert len(notes) == len(reworks) - 1 and notes[0].payload["unchanged"] == [item]


@pytest.mark.parametrize("stem", ["UI step FAIL: bind:tenant -- saw: ", "function grade FAIL: plinket_check -- answered: "])
def test_a_finding_whose_observation_changed_is_not_marked(smoke_blueprint, tmp_path, monkeypatch, stem):  # noqa: F811
    from app.factory.build.ledger import EventKind

    verdicts = [_nc(f"{stem}{n}") for n in range(6)]
    r, handed = _run_rounds(monkeypatch, tmp_path, smoke_blueprint, verdicts)
    reworks = [h for h in handed if h]
    assert len(reworks) >= 2
    assert not any(runner_mod.UNCHANGED_NOTE in i for h in reworks for i in h)
    assert not [e for e in r.ledger.events() if e.kind is EventKind.NOTE and (e.payload or {}).get("unchanged")]


# --- run_grader: the public path ------------------------------------------

@pytest.mark.parametrize("step, saw, cap, why", [
    ("load:/", "Timeout 5000ms exceeded", "invoice_total", "rc=2"),
    ("route:/v1/visit_log", "status 404", "visit_log", "two inputs gave one result"),
])
def test_run_grader_hands_each_failure_on_with_its_evidence(
    tmp_path, independent_grader, monkeypatch, step, saw, cap, why  # noqa: F811
):
    row = {"name": step, "status": "failed", "expected": "GET answers 200", "saw": saw}

    class _Ui:
        ran = True
        errors: list = []
        ok = False
        failing = [step]
        not_run: list = []
        steps = [row, {"name": "other:step", "status": "passed"}]

        def to_dict(self):
            return {"ok": False, "ran": True, "failing": [step], "steps": self.steps}

    monkeypatch.setattr(
        grader, "_grade_ui", lambda blueprint, root, gradroot, base_url, brief=None: (_Ui(), True, ""),
    )
    real = fg.grade_functions

    def graded(*a, **kw):
        grade = real(*a, **kw)
        bad = fg.FunctionResult(cap, False, [], [], why, probe={"inputs": [{"n": 1}], "answers": [{"t": 7}]})
        grade.functions = [f for f in grade.functions if f.capability_id != cap] + [bad]
        return grade

    monkeypatch.setattr(fg, "grade_functions", graded)
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    verdict = grader.run_grader(
        plan_capabilities=IDS, blueprint=_bp(IDS), workspace=ws, product_id="invented-product",
        writer_run=WRITER_RUN,
    )
    assert f"UI step FAIL: {step} -- expected: GET answers 200; saw: {saw}" in verdict.writer_items
    fn_item = (f"function grade FAIL: {cap} -- why: {why}; "
               'called with: [{"n": 1}]; answered: [{"t": 7}]')
    assert fn_item in verdict.writer_items
    assert not any(i.startswith("UI step FAIL: other:step") for i in verdict.writer_items)


@pytest.mark.parametrize("bp, words", [(BPS[0], "header"), (BPS[1], "sent Bearer")])
def test_grade_ui_redacts_its_own_bearer_from_the_failure_text(tmp_path, bp, words):
    import json
    from pathlib import Path

    names = [st.name for st in ug.ui_plan(bp, NO_DOCS)]
    seen = {}

    def fake(cmd, **kw):
        env = kw["env"]
        seen["bearer"] = env[ug.BEARER_ENV]
        results = {n: [{"status": "passed"}] for n in names}
        results[names[0]] = [{"status": "failed", "error": {"message": f"{words} {env[ug.BEARER_ENV]}"}}]
        Path(env["PLAYWRIGHT_JSON_OUTPUT_NAME"]).write_text(json.dumps(_report(results)), encoding="utf-8")

    g = ug.grade_ui(bp, tmp_path, base_url="http://x", run=fake, brief=NO_DOCS)
    row = {r["name"]: r for r in g.steps}[names[0]]
    assert seen["bearer"] and seen["bearer"] not in row["saw"]
    assert row["saw"] == f"{words} {ug.REDACTED}"


@pytest.mark.parametrize("a, b", [("UI step FAIL: load:/ -- saw: x", "UI step FAIL: load:/ -- saw: y"),
                                  ("function grade FAIL: cap_q -- why: rc=1", "function grade FAIL: cap_q -- why: rc=4")])
def test_unchanged_is_against_the_round_just_before_not_an_older_one(
    smoke_blueprint, tmp_path, monkeypatch, a, b  # noqa: F811
):
    r, handed = _run_rounds(monkeypatch, tmp_path, smoke_blueprint, [_nc(a), _nc(b), _nc(a), _nc(a)] + [_nc(a)] * 2)
    reworks = [h for h in handed if h]
    assert reworks[:3] == [(a,), (b,), (a,)]
