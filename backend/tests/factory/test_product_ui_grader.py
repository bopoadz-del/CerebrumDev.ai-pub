"""F-8b: the UI grader script is blueprint-derived and a skipped run fails."""

from __future__ import annotations

import dataclasses
import json
import subprocess

import pytest

import app.factory.build.grader as grader
import app.factory.build.product_grader.grade_md as gm
import app.factory.build.product_grader.ui_grader as ug

BPS = [
    {"product_id": "lettings-desk", "product_name": "Lettings Desk", "summary": "tenancy files",
     "ui_modules": ["tenant_portal"], "capabilities": [{"id": "rent_roll", "description": "Roll rent."}, {"id": "notice_log", "description": "Log notices."}]},
    {"product_id": "site-ops", "product_name": "Site Ops", "summary": "inspections",
     "ui_modules": ["command_center", "map"], "capabilities": [{"id": "defect_register", "description": "Raise defects."}]},
]


def _report(names, failed=()):
    return {"suites": [{"specs": [{"title": n, "tests": [{"results": [{"status": "failed" if n in failed else "passed"}]}]} for n in names]}]}


@pytest.mark.parametrize("bp", BPS)
def test_plan_covers_every_declared_route_and_screen(bp):
    names = [s.name for s in ug.ui_plan(bp)]
    for c in bp["capabilities"]:
        assert f"route:/v1/{c['id']}" in names
    for m in bp["ui_modules"]:
        assert f"screen:{m}" in names
    assert {s.kind for s in ug.ui_plan(bp)} >= set(ug.STEP_KINDS)


def test_two_blueprints_two_scripts():
    a, b = (ug.render_spec(bp) for bp in BPS)
    assert a != b
    assert "rent_roll" in a and "rent_roll" not in b


@pytest.mark.parametrize("bp", BPS)
def test_all_passed_is_ok(bp):
    g = ug.parse_report(_report([s.name for s in ug.ui_plan(bp)]), bp)
    assert g.ok


@pytest.mark.parametrize("bp", BPS)
def test_failed_or_missing_step_is_named(bp):
    names = [s.name for s in ug.ui_plan(bp)]
    g = ug.parse_report(_report(names[1:], failed=[names[-1]]), bp)
    assert not g.ok and g.failing == [names[0], names[-1]]


@pytest.mark.parametrize("base_url", [None, ""])
def test_grader_mutant_skip_ui_run_fails(tmp_path, base_url):
    g = ug.grade_ui(BPS[0], tmp_path, base_url=base_url)
    assert not g.ok and not g.ran


def test_run_without_report_fails(tmp_path):
    g = ug.grade_ui(BPS[1], tmp_path, base_url="http://x", run=lambda *a, **k: None)
    assert not g.ok


def test_run_with_report_is_parsed(tmp_path):
    bp = BPS[1]

    def fake(cmd, **kw):
        names = [s.name for s in ug.ui_plan(bp)]
        open(kw["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"], "w").write(json.dumps(_report(names)))

    g = ug.grade_ui(bp, tmp_path, base_url="http://x", run=fake)
    assert g.ok
    assert (tmp_path / "fixtures" / "fixture.pdf").read_bytes().startswith(b"%PDF")
    assert (tmp_path / "fixtures" / "fixture.xlsx").is_file()


def test_run_that_prints_report_to_stdout_is_parsed(tmp_path):
    # The JSON reporter may print to stdout instead of honouring the
    # PLAYWRIGHT_JSON_OUTPUT_NAME env; the captured stdout is a fallback.
    from types import SimpleNamespace

    bp = BPS[1]

    def fake(cmd, **kw):
        names = [s.name for s in ug.ui_plan(bp)]
        return SimpleNamespace(stdout=json.dumps(_report(names)))

    g = ug.grade_ui(bp, tmp_path, base_url="http://x", run=fake)
    assert g.ok


@pytest.mark.parametrize("bp", BPS)
def test_fixtures_come_from_blueprint(tmp_path, bp):
    fx = ug.write_fixtures(bp, tmp_path)
    assert bp["product_name"].encode() in fx["pdf"].read_bytes()


# --- G2 survivors M1-M7 ---------------------------------------------------

@pytest.mark.parametrize("statuses,expected", [
    (["passed", "passed"], True),
    (["passed", "failed"], False),   # M1: any() would pass this
    (["failed", "passed"], False),
    ([], False),                     # M2: empty results never pass
])
def test_step_passed_needs_every_result(statuses, expected):
    assert ug.step_passed([{"status": x} for x in statuses]) is expected


@pytest.mark.parametrize("bp", BPS)
def test_report_with_mixed_retries_or_empty_step_fails(bp):
    names = [s.name for s in ug.ui_plan(bp)]
    rep = _report(names)
    specs = rep["suites"][0]["specs"]
    specs[0]["tests"][0]["results"].append({"status": "failed"})
    specs[1]["tests"][0]["results"] = []
    g = ug.parse_report(rep, bp)
    assert g.failing == [names[0], names[1]]


@pytest.mark.parametrize("status,honest", [(422, True), (503, True), (200, False), (500, False)])
def test_honest_error_never_accepts_200(status, honest):  # M3
    assert (status in ug.HONEST_ERROR_STATUSES) is honest
    assert f"expect({list(ug.HONEST_ERROR_STATUSES)}".replace(" ", "") in ug.KIND_JS["honest_error"].replace(" ", "")


@pytest.mark.parametrize("exc", [OSError("boom"), TimeoutError("slow"), RuntimeError("x")])
def test_run_exception_is_a_recorded_fail(tmp_path, exc):  # M4
    def boom(*a, **k):
        raise exc

    g = ug.grade_ui(BPS[0], tmp_path, base_url="http://x", run=boom)
    assert not g.ok and any(type(exc).__name__ in e for e in g.errors)


@pytest.mark.parametrize("kind,needle", [
    ("route", "toBe(200)"),                 # M5
    ("load", "toBe(200)"),
    ("screen", "toBe(200)"),
    ("screen", "expect(errs).toEqual([])"),  # M6
    ("load", "expect(errs).toEqual([])"),
])
def test_kind_assertions(kind, needle):
    assert needle in ug.KIND_JS[kind]


@pytest.mark.parametrize("bp", BPS)
def test_spec_has_a_body_for_every_planned_kind(bp):
    spec = ug.render_spec(bp)
    for kind in {s.kind for s in ug.ui_plan(bp)}:
        assert f'(s.kind === "{kind}")' in spec
        assert ug.KIND_JS[kind].splitlines()[0] in spec


@pytest.mark.parametrize("bp", BPS)
def test_xlsx_fixture_rows_come_from_blueprint(tmp_path, bp):  # M7
    from openpyxl import load_workbook

    ws = load_workbook(ug.write_fixtures(bp, tmp_path)["xlsx"]).active
    rows = [r for r in ws.iter_rows(values_only=True)][1:]
    assert [r[0] for r in rows] == [c["id"] for c in bp["capabilities"]]
    assert [r[1] for r in rows] == [c["description"] for c in bp["capabilities"]]
    assert ws.title == ug._slug(bp["product_id"])


def test_two_blueprints_two_xlsx(tmp_path):
    from openpyxl import load_workbook

    a, b = (load_workbook(ug.write_fixtures(bp, tmp_path / str(i))["xlsx"]).active for i, bp in enumerate(BPS))
    assert list(a.iter_rows(values_only=True)) != list(b.iter_rows(values_only=True))


# --- G2 U14-U17, U21 ------------------------------------------------------

@pytest.mark.parametrize("bp", BPS)
def test_spec_resolves_every_target_against_base_url(bp):  # U21
    spec = ug.render_spec(bp)
    assert "process.env.BASE_URL" in spec and "new URL(t, BASE)" in spec
    for call in ("page.goto(", "request.get(", "request.post("):
        for chunk in spec.split(call)[1:]:
            assert chunk.startswith("url("), call


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "https://p.example.test/app/"])
def test_grade_ui_passes_base_url_to_the_run(tmp_path, url):
    seen = {}
    ug.grade_ui(BPS[0], tmp_path, base_url=url, run=lambda cmd, **kw: seen.update(kw["env"]))
    assert seen["BASE_URL"] == url


@pytest.mark.parametrize("kind,needles", [
    ("bind", ["s.target", "toBeVisible()"]),                                   # U14
    ("attach_pdf", ["name: '+'", "setInputFiles", "GRADE_FIXTURES"]),          # U15
    ("attach_xlsx", ["name: '+'", "setInputFiles", "GRADE_FIXTURES"]),
    ("parsed", ["/parsed/i", "toBeVisible"]),                                   # U16
    ("cited_answer", ["QUESTION", "citation", "getByText(/", "toBeVisible"]),           # U17
])
def test_kind_bodies_pinned(kind, needles):
    for n in needles:
        assert n in ug.KIND_JS[kind], (kind, n)


@pytest.mark.parametrize("bp", BPS)
def test_attach_targets_are_the_written_fixtures(tmp_path, bp):
    fx = ug.write_fixtures(bp, tmp_path)
    targets = {s.target for s in ug.ui_plan(bp) if s.kind.startswith("attach_")}
    assert targets == {fx["pdf"].name, fx["xlsx"].name}


# --- HTTP status asserts, executed for real (G2 U5, U12, U13, U15b, U16) ----
import sys as _sys  # noqa: E402
from pathlib import Path as _P  # noqa: E402

_sys.path.insert(0, str(_P(__file__).parent))
import ui_spec_harness as h  # noqa: E402

BP = BPS[0]
ROUTES = [f"/v1/{c['id']}" for c in BP["capabilities"]]
HONEST = f"POST /v1/{BP['capabilities'][0]['id']}"


@pytest.mark.parametrize("statuses,want", [
    ({}, {"load:/": "passed", "screen:tenant_portal": "passed"}),
    ({"/": 500}, {"load:/": "failed", "screen:tenant_portal": "failed"}),
    ({"/": 201}, {"load:/": "failed", "screen:tenant_portal": "failed"}),
    ({"/other": 500}, {"load:/": "passed", "screen:tenant_portal": "passed"}),
])
def test_page_steps_need_exactly_200(tmp_path, statuses, want):  # U5 / U12
    # A URL fragment never reaches the server, so the screen goes to "/".
    with h.server(statuses) as base:
        got = h.run_spec(ug.render_spec(BP), tmp_path, base, only_kinds=("load", "screen"))
    assert {k: got[k].split(":")[0] for k in want} == want
    if want.get("load:/") == "failed":
        assert f"expected 200 got {statuses['/']}" in got["load:/"]


@pytest.mark.parametrize("bad,code", [(ROUTES[0], 404), (ROUTES[1], 500), (ROUTES[1], 204)])
def test_every_route_needs_exactly_200(tmp_path, bad, code):  # U13
    with h.server({bad: code}) as base:
        got = h.run_spec(ug.render_spec(BP), tmp_path, base, only_kinds=("route",))
    assert got == {f"route:{r}": ("passed" if r != bad else f"failed: expected 200 got {code}") for r in ROUTES}


@pytest.mark.parametrize("code,ok", [(422, True), (503, True), (200, False), (400, False), (500, False)])
def test_honest_error_needs_422_or_503_never_200(tmp_path, code, ok):  # U15b
    with h.server({HONEST: code}) as base:
        got = h.run_spec(ug.render_spec(BP), tmp_path, base, only_kinds=("honest_error",))
    (v,) = got.values()
    assert (v == "passed") is ok


@pytest.mark.parametrize("pair", [({}, {ROUTES[0]: 500}), ({ROUTES[1]: 404}, {})])
def test_two_base_urls_two_runs(tmp_path, pair):  # U16: BASE_URL is what is graded
    runs = []
    for i, statuses in enumerate(pair):
        (tmp_path / str(i)).mkdir()
        with h.server(statuses) as base:
            runs.append(h.run_spec(ug.render_spec(BP), tmp_path / str(i), base, only_kinds=("route",)))
    assert runs[0] != runs[1]
    for statuses, run in zip(pair, runs):
        assert sorted(k for k, v in run.items() if v != "passed") == sorted(f"route:{r}" for r in statuses)


@pytest.mark.parametrize("base", [None, ""])
def test_no_base_url_throws_not_relative(tmp_path, base):  # U16
    got = h.run_spec(ug.render_spec(BP), tmp_path, base, only_kinds=("route",))
    assert "BASE_URL is not set" in got["__error__"]


# --- G2 15bd9c1 gaps on split A: U5 nested suites, U12 bind, U13 target ---
@pytest.mark.parametrize("depth", [1, 3])
def test_nested_report_suites_are_read(depth):  # U5
    names = [s.name for s in ug.ui_plan(BP)]
    suite = {"specs": [{"title": n, "tests": [{"results": [{"status": "passed"}]}]} for n in names]}
    for _ in range(depth):
        suite = {"suites": [suite]}
    g = ug.parse_report({"suites": [suite]}, BP)
    assert g.ok and [s["passed"] for s in g.steps] == [True] * len(names)


@pytest.mark.parametrize("i,served,ok", [(0, 0, True), (1, 1, True), (0, 1, False), (1, 0, False)])
def test_bind_needs_the_products_own_id_on_the_page(tmp_path, i, served, ok):  # U12
    bp = BPS[i]
    with h.server({}, bodies={"/": f"<h1>tenant {BPS[served]['product_id']}</h1>"}) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=("load", "bind"))
    assert got["load:/"] == "passed" and (got["bind:tenant"] == "passed") is ok


@pytest.mark.parametrize("i", [0, 1])
def test_honest_error_targets_the_products_first_route(tmp_path, i):  # U13
    bp = BPS[i]
    first = bp["capabilities"][0]["id"]
    with h.server({f"POST /v1/{first}": 422}) as base:  # every other POST answers 200
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=("honest_error",))
        assert [k for k, _ in base.seen] == [f"/v1/{first}"]
    assert got == {f"honest_error:/v1/{first}": "passed"}


@pytest.mark.parametrize("which", [None, ""])
def test_missing_node_fails_never_skips(tmp_path, monkeypatch, which):
    monkeypatch.setattr(h.shutil, "which", lambda name: which or None)
    with pytest.raises(pytest.fail.Exception, match="never a skip"):
        h.run_spec(ug.render_spec(BP), tmp_path, "http://127.0.0.1:9")


# --- merged from split B (#83): N1, N2, N4-N6, U18, U19 ---
PLANNED = sorted({s.kind for bp in BPS for s in ug.ui_plan(bp)})


@pytest.mark.parametrize("drop", [("route",), ("screen",), ("honest_error",), ("load", "parsed"), ("attach_pdf", "bind", "cited_answer")])
def test_missing_kind_is_a_hard_error_naming_every_kind(monkeypatch, drop):  # N1 / N2
    monkeypatch.setattr(ug, "KIND_JS", {k: v for k, v in ug.KIND_JS.items() if k not in drop})
    with pytest.raises(ValueError) as e:
        ug.render_spec(BPS[1])
    assert str(e.value) == f"no UI assertion for step kinds: {sorted(drop)}"


@pytest.mark.parametrize("extra", ["unused_kind", "another"])
def test_extra_kind_bodies_are_not_an_error(monkeypatch, extra):  # N4
    monkeypatch.setattr(ug, "KIND_JS", {**ug.KIND_JS, extra: "expect(1).toBe(1);"})
    assert f"s.kind === {json.dumps(extra)}" in ug.render_spec(BPS[0])


@pytest.mark.parametrize("bp", BPS)
def test_every_planned_kind_has_exactly_one_branch(bp):  # N5
    spec = ug.render_spec(bp)
    for k in {s.kind for s in ug.ui_plan(bp)}:
        assert spec.count(f"(s.kind === {json.dumps(k)})") == 1
    assert spec.count("} else { throw new Error('no assertion for ' + s.kind); }") == 1


@pytest.mark.parametrize("bp", BPS)
def test_planned_kinds_all_have_bodies(bp):  # N6
    assert {s.kind for s in ug.ui_plan(bp)} <= set(ug.KIND_JS)


def _with_plan_kind(spec: str, name: str, kind: str) -> str:
    plan_line = next(line for line in spec.splitlines() if line.startswith("const PLAN = "))
    plan = json.loads(plan_line[len("const PLAN = "):-1])
    plan.append({"name": name, "kind": kind, "target": "/"})
    return spec.replace(plan_line, f"const PLAN = {json.dumps(plan)};")


@pytest.mark.parametrize("kind", ["mystery", "routee"])
def test_runtime_unknown_kind_throws_never_passes(tmp_path, kind):  # U18
    spec = _with_plan_kind(ug.render_spec(BPS[0]), f"odd:{kind}", kind)
    with h.server({}) as base:
        got = h.run_spec(spec, tmp_path, base, only_kinds=(kind, "route"))
    assert got[f"odd:{kind}"] == f"failed: no assertion for {kind}"
    assert all(v == "passed" for k, v in got.items() if k.startswith("route:"))


@pytest.mark.parametrize("kind,statuses,failed", [
    ("route", {"/v1/rent_roll": 500}, {"route:/v1/rent_roll"}),
    ("load", {"/": 500}, {"load:/"}),
    ("honest_error", {"POST /v1/rent_roll": 200}, {"honest_error:/v1/rent_roll"}),
])
def test_each_kind_runs_its_own_branch(tmp_path, kind, statuses, failed):  # U19
    with h.server(statuses) as base:
        got = h.run_spec(ug.render_spec(BPS[0]), tmp_path, base, only_kinds=(kind,))
    assert {k for k, v in got.items() if v != "passed"} == failed and got


# --- G2 15bd9c1 gaps on split B: N4-N6, U18, U19 executed end to end -------
def _flow(tmp_path, bp, *, page="", upload="parsed ok", answer="total 1,240 [1]"):
    fx = ug.write_fixtures(bp, tmp_path / "fx")
    with h.server({}, bodies={"/": page, "POST /__upload": upload, "POST /__ask": answer}) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, fixtures=fx["pdf"].parent,
                         only_kinds=("load", "bind", "attach_pdf", "attach_xlsx", "parsed", "cited_answer"))
        return got, list(base.seen), fx


@pytest.mark.parametrize("bp", BPS)
def test_full_flow_passes_on_a_product_that_answers(tmp_path, bp):
    got, seen, fx = _flow(tmp_path, bp, page=f"tenant {bp['product_id']}")
    assert set(got.values()) == {"passed"}, got
    uploads = [(k, b) for k, b in seen if k.startswith("/__upload")]
    assert [k for k, _ in uploads] == ["/__upload?name=fixture.pdf", "/__upload?name=fixture.xlsx"]  # N6
    assert uploads[0][1] == fx["pdf"].read_bytes() and uploads[1][1] == fx["xlsx"].read_bytes()
    assert bp["summary"].encode() in uploads[0][1]  # U19: the blueprint's own summary reaches the product
    asked = [b.decode() for k, b in seen if k == "/__ask"]
    assert asked == [ug._question(bp)]  # U18
    assert bp["capabilities"][0]["description"].split(".")[0] in asked[0]


@pytest.mark.parametrize("bp", BPS)
def test_bind_fails_without_the_product_id(tmp_path, bp):  # N4
    got, _, _ = _flow(tmp_path, bp, page="tenant somebody-else")
    assert got["bind:tenant"].startswith("failed")


@pytest.mark.parametrize("upload", ["", "upload received"])
def test_parsed_fails_without_a_parsed_status(tmp_path, upload):  # N5
    got, _, _ = _flow(tmp_path, BPS[0], page="lettings-desk", upload=upload)
    assert got["parsed:status"].startswith("failed") and got["attach:pdf"] == "passed"


@pytest.mark.parametrize("answer,ok", [("total 1,240 [1]", True), ("I cannot say", False), ("see [2]", True)])
def test_cited_answer_needs_a_citation_and_a_number(tmp_path, answer, ok):
    got, _, _ = _flow(tmp_path, BPS[1], page="site-ops", answer=answer)
    assert (got["answer:cited"] == "passed") is ok


@pytest.mark.parametrize("pair", [(0, 1)])
def test_two_blueprints_two_questions_and_fixtures(tmp_path, pair):  # U18 / U19 two inputs
    runs = [_flow(tmp_path / str(i), BPS[i], page=BPS[i]["product_id"]) for i in pair]
    asked = [[b for k, b in seen if k == "/__ask"] for _, seen, _ in runs]
    pdfs = [[b for k, b in seen if k == "/__upload?name=fixture.pdf"] for _, seen, _ in runs]
    assert asked[0] != asked[1] and pdfs[0] != pdfs[1]



# --- 'playwright report has no results' is an error, and only for an empty report ---
@pytest.mark.parametrize("report,empty", [
    ({"suites": []}, True),
    ({"suites": [{"specs": []}]}, True),
    ({}, True),
    ({"suites": [{"specs": [{"title": "load:/", "tests": [{"results": [{"status": "passed"}]}]}]}]}, False),
])
def test_empty_report_is_a_named_error(report, empty):
    g = ug.parse_report(report, BPS[0])
    assert g.errors == (["playwright report has no results"] if empty else [])
    assert ("playwright report has no results" in g.to_dict()["failing"]) is empty and not (empty and g.ok)


# --- a step that did not run is not a failure of its own ---------------------

DOC_BP = BPS[0]


def _chain_report(statuses):
    """A Playwright report giving each planned step the status named for it
    (``passed`` when the step is not named)."""
    return {"suites": [{"specs": [
        {"title": st.name, "tests": [{"results": [{"status": statuses.get(st.name, "passed")}]}]}
        for st in ug.ui_plan(DOC_BP)
    ]}]}


@pytest.mark.parametrize("failed_step,skipped", [
    ("attach:pdf", ["attach:xlsx", "parsed:status", "answer:cited"]),
    ("parsed:status", ["answer:cited"]),
])
def test_chain_steps_skipped_after_a_failure_are_not_run_not_failed(failed_step, skipped):
    statuses = {failed_step: "failed", **{name: "skipped" for name in skipped}}
    g = ug.parse_report(_chain_report(statuses), DOC_BP)
    assert g.failing == [failed_step]
    assert [(r["name"], r["blocked_by"]) for r in g.not_run] == [(n, failed_step) for n in skipped]
    assert not g.ok
    assert g.to_dict()["not_run"] == [{"name": n, "blocked_by": failed_step} for n in skipped]


@pytest.mark.parametrize("failed_step,skipped", [
    ("attach:pdf", ["attach:xlsx", "parsed:status", "answer:cited"]),
    ("parsed:status", ["answer:cited"]),
])
def test_not_run_steps_reach_the_reasons_and_grade_md_as_not_run(failed_step, skipped):
    """The record a real grade writes: the one step that failed is the only
    UI failure class; each step it blocked is ungraded and is listed as NOT
    RUN with its blocker in GRADE.md."""
    statuses = {failed_step: "failed", **{name: "skipped" for name in skipped}}
    ui = ug.parse_report(_chain_report(statuses), DOC_BP).to_dict()
    record = {"layer1": {"ui": ui}, "layer2": {"ran": True}}
    rows = [r for r in grader.reason_classes(record, []) if r["layer"] == "ui"]
    assert sorted(r["class"] for r in rows) == sorted(["failed_ui_step"] + ["ungraded"] * len(skipped))
    md = gm.render_md({"product_id": "p", "layer1": {"ui": ui}, "layer2": {}})
    assert [line for line in md.splitlines() if line.startswith("  - NOT RUN: ")] == [
        f"  - NOT RUN: {n} (blocked by {failed_step})" for n in skipped
    ]
    assert [line for line in md.splitlines() if line.startswith("  - FAIL: ")] == [f"  - FAIL: {failed_step}"]


@pytest.mark.parametrize("name", ["bind:tenant", "screen", "route"])
def test_a_skipped_step_outside_the_chain_is_still_a_failure(name):
    """Only a chain step can be blocked by another step. Any other step that
    Playwright reports as skipped did not pass and nothing explains it."""
    target = next(s.name for s in ug.ui_plan(DOC_BP) if s.name.startswith(name))
    g = ug.parse_report(_chain_report({target: "skipped"}), DOC_BP)
    assert g.failing == [target] and g.not_run == []


@pytest.mark.parametrize("step", ["parsed:status", "answer:cited"])
def test_a_skipped_chain_step_with_no_failed_step_before_it_is_a_failure(step):
    g = ug.parse_report(_chain_report({step: "skipped"}), DOC_BP)
    assert g.failing == [step] and g.not_run == []


@pytest.mark.parametrize("report", [None, ["load:/"]])
def test_a_report_that_is_not_a_mapping_has_no_statuses(report):
    """A report that is not a JSON object names no step: nothing ran."""
    assert ug.report_statuses(report) == {}
    g = ug.parse_report(report, DOC_BP)
    assert g.errors == ["playwright report has no results"] and not g.ok


@pytest.mark.parametrize("bp", BPS)
def test_only_the_document_chain_stops_at_its_first_failure(bp):
    """One failing step must not stop the steps that navigate by themselves:
    the serial block holds the document chain and nothing else."""
    spec = ug.render_spec(bp)
    steps_at = spec.index("test.describe('steps'")
    chain_at = spec.index("test.describe('document chain'")
    assert spec.count("mode: 'serial'") == 1
    assert steps_at < chain_at < spec.index("mode: 'serial'")
    assert "!CHAIN.includes(s.kind)" in spec[steps_at:chain_at]


@pytest.mark.parametrize("bp", BPS)
def test_screens_and_routes_are_observed_when_bind_fails(tmp_path, bp):
    """A product that answers every screen and route but never shows its id:
    bind fails, and every other step still reports what it saw."""
    with h.server({}, bodies={"/": "<h1>welcome</h1>"}) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=("load", "bind", "screen", "route"))
    assert got["bind:tenant"].startswith("failed")
    others = [name for name in got if name != "bind:tenant"]
    assert others and all(got[name] == "passed" for name in others)


@pytest.mark.parametrize("bp", BPS)
def test_a_whole_file_serial_spec_would_hide_screens_and_routes(tmp_path, bp):
    """The stand-in keeps Playwright's serial rule, so the split is what the
    test above observes: the same product under a spec made serial as a whole
    file (the old shape) gets every step after bind skipped, not run."""
    serial_file = ug.render_spec(bp).replace(
        "\n", "\ntest.describe.configure({ mode: 'serial' });\n", 1
    )
    with h.server({}, bodies={"/": "<h1>welcome</h1>"}) as base:
        got = h.run_spec(serial_file, tmp_path, base, only_kinds=("load", "bind", "screen", "route"))
    after = [name for name in got if name not in ("load:/", "bind:tenant")]
    assert got["bind:tenant"].startswith("failed")
    assert after and all(got[name] == "skipped" for name in after)


@pytest.mark.parametrize("bp", BPS)
def test_bind_opens_the_front_page_itself(tmp_path, bp):
    """bind does not rely on the load step having run before it."""
    page = f"<h1>{bp['product_id']}</h1>"
    with h.server({}, bodies={"/": page}) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=("bind",))
    assert got == {"bind:tenant": "passed"}


# --- the steps call the product with the grading boot's bearer --------------
from app.factory.build.authority import capability_route as _route  # noqa: E402
from app.factory.build.image_sufficiency import GATE_RUN_ENV, GATE_TOKEN_VAR  # noqa: E402

_API_KINDS = ("load", "screen", "route", "honest_error")


def _honest(bp):
    return {f"POST {_route(bp['capabilities'][0]['id'])}": 422}


@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("protected", [True, False])
def test_a_product_with_or_without_sign_in_passes_with_the_grading_bearer(tmp_path, bp, protected):
    """One product answers 401 to any request without the boot's bearer, the
    other needs none: under the grading bearer both pass every page, route
    and honest-error step, and every request the steps made carried it."""
    token = ug.grading_bearer()
    with h.server(_honest(bp), token=token if protected else None) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=_API_KINDS, bearer=token)
    assert got and all(v == "passed" for v in got.values()), got
    assert base.auth and set(base.auth) == {f"Bearer {token}"}


@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("bearer", ["", "not-the-boot-token"])
def test_a_protected_product_called_without_its_bearer_fails_naming_status_and_route(tmp_path, bp, bearer):
    """Without the boot's bearer (none, or a wrong one) the protected product
    answers 401, and each failed step names its route and the status seen."""
    with h.server(_honest(bp), token=ug.grading_bearer()) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=_API_KINDS, bearer=bearer)
    routes = [f"route:{_route(c['id'])}" for c in bp["capabilities"]]
    for name in routes + ["load:/"]:
        assert got[name] == "failed: expected 200 got 401", (name, got)
    honest = f"honest_error:{_route(bp['capabilities'][0]['id'])}"
    assert got[honest].startswith("failed") and "401" in got[honest]


@pytest.mark.parametrize("env,want", [
    (None, GATE_RUN_ENV[GATE_TOKEN_VAR]),
    ({GATE_TOKEN_VAR: " other-token "}, "other-token"),
    ({GATE_TOKEN_VAR: None}, ""),
    ({}, ""),
    ({"SOME_OTHER_VAR": "x"}, ""),
])
def test_grading_bearer_is_the_gate_run_token(env, want):
    assert ug.grading_bearer(env) == want


@pytest.mark.parametrize("bearer,want", [
    (None, GATE_RUN_ENV[GATE_TOKEN_VAR]),
    ("given-token", "given-token"),
    ("", None),
])
def test_grade_ui_hands_the_bearer_to_the_run(tmp_path, bearer, want):
    seen = {}
    kw = {} if bearer is None else {"bearer": bearer}
    ug.grade_ui(BPS[0], tmp_path, base_url="http://127.0.0.1:9", run=lambda cmd, **k: seen.update(k["env"]), **kw)
    assert seen.get(ug.BEARER_ENV) == want


@pytest.mark.parametrize("bearer,want", [("", None), ("abc", "abc"), ("dev-x", "dev-x")])
def test_playwright_run_sets_the_bearer_only_when_given(tmp_path, bearer, want):
    seen = {}
    ug._playwright_run(lambda cmd, **k: seen.update(k["env"]), tmp_path / "s.spec.ts", base_url="http://b",
                       out=tmp_path / "o.json", fixtures=tmp_path, timeout=120, bearer=bearer)
    assert seen.get(ug.BEARER_ENV) == want
    assert seen["BASE_URL"] == "http://b"


def test_the_spec_never_carries_the_token_itself():
    for bp in BPS:
        spec = ug.render_spec(bp)
        assert GATE_RUN_ENV[GATE_TOKEN_VAR] not in spec
        assert f"process.env.{ug.BEARER_ENV}" in spec


@pytest.mark.parametrize("brief", [12345, ["not", "a", "brief"]])
def test_grade_ui_with_an_unreadable_brief_runs_nothing(tmp_path, brief):
    """A value that is not a customer brief stops the UI grade before any
    run: the bearer is never handed to a run that should not happen."""
    calls = []
    got = ug.grade_ui(BPS[0], tmp_path, base_url="http://127.0.0.1:9",
                      run=lambda cmd, **k: calls.append(k), brief=brief)
    assert calls == []
    assert got.errors == [ug.BRIEF_UNREADABLE] and not got.ran


# --- the run's bearer never stays in what the run wrote down ----------------
def _leaky_run(token: str, *, report: bool):
    """A run whose call log names the request headers, the way Playwright's
    does when a request cannot connect: into the JSON report (or, with no
    report, only stderr) and an error-context file next to it."""
    def run(cmd, **kw):
        wd = _P(kw["cwd"])
        log = f"Authorization: Bearer {token}"
        if report:
            body = {"suites": [{"title": "grade_ui.spec.ts", "specs": [{"title": "load:/", "ok": False, "tests": [
                {"results": [{"status": "failed", "error": {"message": log}}]}]}]}]}
            _P(kw["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"]).write_text(json.dumps(body), encoding="utf-8")
        ctx = wd / "test-results" / "load" / "error-context.md"
        ctx.parent.mkdir(parents=True, exist_ok=True)
        ctx.write_text(log, encoding="utf-8")
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": log})()
    return run


@pytest.mark.parametrize("token", ["tok-one-123", "another.token.456"])
def test_the_bearer_is_redacted_from_the_report_and_every_file_the_run_wrote(tmp_path, token):
    g = ug.grade_ui(BPS[0], tmp_path, base_url="http://127.0.0.1:9", run=_leaky_run(token, report=True), bearer=token)
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written and not any(token.encode() in p.read_bytes() for p in written)
    assert ug.REDACTED in (tmp_path / "report.json").read_text(encoding="utf-8")
    assert ug.REDACTED in (tmp_path / "test-results" / "load" / "error-context.md").read_text(encoding="utf-8")
    assert token not in json.dumps(dataclasses.asdict(g))


@pytest.mark.parametrize("token", ["tok-one-123", "another.token.456"])
def test_the_bearer_is_redacted_from_the_quoted_stderr_when_no_report_came(tmp_path, token):
    g = ug.grade_ui(BPS[0], tmp_path, base_url="http://127.0.0.1:9", run=_leaky_run(token, report=False), bearer=token)
    (err,) = g.errors
    assert "no playwright report" in err and token not in err and ug.REDACTED in err


def _run_then(exc: BaseException, token: str):
    """A run that writes the bearer the way Playwright's call log does, then
    ends with ``exc`` (a subprocess timeout, a crash) instead of returning."""
    leak = _leaky_run(token, report=True)

    def run(cmd, **kw):
        leak(cmd, **kw)
        raise exc
    return run


@pytest.mark.parametrize("token", ["dev-local-token", "tok-XYZ-123"])
@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["npx"], 5), OSError("node died")])
def test_the_bearer_is_scrubbed_when_the_run_times_out_or_crashes(tmp_path, token, exc):
    g = ug.grade_ui(BPS[0], tmp_path, base_url="http://127.0.0.1:9", run=_run_then(exc, token), bearer=token)
    assert g.errors == [f"UI run did not happen: {type(exc).__name__}"] and not g.ran
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert (tmp_path / "report.json") in written
    assert not any(token.encode() in p.read_bytes() for p in written)
    assert ug.REDACTED in (tmp_path / "test-results" / "load" / "error-context.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("bp", BPS)
def test_an_empty_bearer_sends_no_authorization_header(tmp_path, bp):
    """With no bearer the steps send no Authorization at all (not an empty
    ``Bearer``), and an open product passes every step."""
    with h.server(_honest(bp)) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=_API_KINDS, bearer="")
    assert got and all(v == "passed" for v in got.values()), got
    assert base.auth and set(base.auth) == {""}


@pytest.mark.parametrize("text,secret,want", [
    ("Bearer abc and abc", "abc", f"Bearer {ug.REDACTED} and {ug.REDACTED}"),
    ("nothing here", "abc", "nothing here"),
    ("Bearer abc", "", "Bearer abc"),
    (None, "abc", ""),
])
def test_redact(text, secret, want):
    assert ug.redact(text, secret) == want


@pytest.mark.parametrize("secret", ["s3cret", "other-9"])
def test_scrub_files_rewrites_only_the_files_holding_the_secret(tmp_path, secret):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "hit.json").write_text(f'{{"h": "Bearer {secret}"}}', encoding="utf-8")
    (tmp_path / "clean.txt").write_text("no token", encoding="utf-8")
    assert ug.scrub_files(tmp_path, secret) == ["a/hit.json"]
    assert (tmp_path / "a" / "hit.json").read_text(encoding="utf-8") == f'{{"h": "Bearer {ug.REDACTED}"}}'
    assert (tmp_path / "clean.txt").read_text(encoding="utf-8") == "no token"
    assert ug.scrub_files(tmp_path, "") == []


# --- the scrub runs on every way out of the run ------------------------------
@pytest.mark.parametrize("token", ["tok-one-123", "another.token.456"])
def test_the_bearer_is_scrubbed_even_when_the_run_is_interrupted(tmp_path, token):
    """An interrupt is not a recorded FAIL -- it leaves ``grade_ui`` as it
    came -- and what the run wrote before it is scrubbed all the same."""
    with pytest.raises(KeyboardInterrupt):
        ug.grade_ui(BPS[0], tmp_path, base_url="http://127.0.0.1:9",
                    run=_run_then(KeyboardInterrupt(), token), bearer=token)
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert (tmp_path / "report.json") in written
    assert not any(token.encode() in p.read_bytes() for p in written)


# --- the bearer goes to the product's own origin and nowhere else ------------
@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("bearer", ["the-boot-token", ""])
def test_a_page_request_to_another_origin_never_carries_the_bearer(tmp_path, bp, bearer):
    """The product's page loads one file of its own and one from another
    origin. With a bearer, every request to the product carries it and no
    request to the other origin does; with none, nothing carries one."""
    with h.server({}) as other:
        page = f'<html><script src="/own.js"></script><img src="{other}/pixel.png"></html>'
        with h.server({}, bodies={"/": page}, token=bearer or None) as base:
            got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=("load", "screen"), bearer=bearer)
    assert got and all(v == "passed" for v in got.values()), got
    assert base.auth and set(base.auth) == {f"Bearer {bearer}" if bearer else ""}
    assert other.auth and set(other.auth) == {""}


# --- a screen reached inside the loaded page is still loaded and judged ------
@pytest.mark.parametrize("bp", BPS)
def test_every_screen_after_the_first_page_is_loaded_and_passes_on_an_honest_product(tmp_path, bp):
    """After ``load:/`` every screen's address differs from the loaded
    page's only by its fragment, and a browser makes no request for that
    move. Each screen is still loaded by itself: one request for the first
    page and one for each screen, and every step passes."""
    with h.server({}) as base:
        got = h.run_spec(ug.render_spec(bp), tmp_path, base, only_kinds=("load", "screen"))
    assert got == {"load:/": "passed", **{f"screen:{m}": "passed" for m in bp["ui_modules"]}}
    assert len(base.auth) == 1 + len(bp["ui_modules"])


@pytest.mark.parametrize("status", [500, 404])
def test_a_screen_whose_address_does_not_answer_200_fails_naming_the_status(tmp_path, status):
    with h.server({"/": status}) as base:
        got = h.run_spec(ug.render_spec(BP), tmp_path, base, only_kinds=("load", "screen"))
    assert got["screen:tenant_portal"] == f"failed: expected 200 got {status}"
