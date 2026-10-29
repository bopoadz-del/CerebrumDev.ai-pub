"""F-8c-0: the build-end step writes the product_grade.v1 record the #75
gate reads, from the F-8a / F-8b results, and never certifies by itself."""

from __future__ import annotations

import json

import pytest

from app.factory.build.product_grader import grade_writer as gw

EV = 'page shows "rent roll total 1,240 [1]"'


def _fn(ok=True, ids=("rent_roll", "notice_log", "tenant_add", "deposit_hold")):
    fns = [{"capability_id": c, "two_input_passed": ok, "mutants_killed": ["constant_return", "drop_rule"] if ok else [],
            "mutants_survived": [] if ok else ["drop_rule"], "detail": "", "ok": ok} for c in ids]
    return {"ok": ok, "min_functions": 4, "graded": len(ids), "failing": [] if ok else list(ids), "functions": fns}


def _ui(ok=True, names=("load:/", "route:/v1/rent_roll")):
    steps = [{"name": n, "kind": n.split(":")[0], "target": n, "passed": ok} for n in names]
    return {"ok": ok, "ran": True, "failing": [] if ok else list(names), "steps": steps}


def _l2(*verdicts, ran=True, open_fails=()):
    return {"ran": ran, "verdicts": list(verdicts) or [{"question": "q1", "verdict": "PASS", "evidence": EV}],
            "open_fails": list(open_fails)}


CASES = [
    ("lettings-desk", _fn(), _ui()),
    ("site-ops", _fn(ids=("defect_raise", "crew_roster", "permit_check", "photo_upload", "daily_rollup")),
     _ui(names=("load:/", "screen:map", "route:/v1/defect_raise"))),
]


def _gate_shape_ok(grade):
    """The fields and types #75's certification_refusal loads."""
    return (grade["schema"] == "product_grade.v1" and isinstance(grade["layer1"], dict)
            and isinstance(grade["layer1"]["functions"], dict) and isinstance(grade["layer1"]["ui"], dict)
            and isinstance(grade["layer2"], dict) and isinstance(grade["layer2"]["verdicts"], list))


@pytest.mark.parametrize("pid,fn,ui", CASES)
def test_writes_both_files_in_gate_format(tmp_path, pid, fn, ui):
    g = gw.write_build_grade(tmp_path, product_id=pid, functions=fn, ui=ui, layer2=_l2())
    on_disk = json.loads((tmp_path / "grade.json").read_text("utf-8"))
    assert on_disk == g and _gate_shape_ok(on_disk)
    assert on_disk["product_id"] == pid and on_disk["certified"] is True
    assert on_disk["layer1"]["functions"]["graded"] == fn["graded"]
    assert [s["name"] for s in on_disk["layer1"]["ui"]["steps"]] == [s["name"] for s in ui["steps"]]
    md = (tmp_path / "GRADE.md").read_text("utf-8").splitlines()
    assert md[0] == f"# GRADE -- {pid}" and "## Layer 1 (deterministic): GREEN" in md
    assert f"- UI steps passed: {len(ui['steps'])}/{len(ui['steps'])}" in md
    assert not (tmp_path / "grade.json.tmp").exists()


@pytest.mark.parametrize("pid,fn,ui", CASES)
def test_two_products_two_records(tmp_path, pid, fn, ui):
    other = CASES[1] if pid == CASES[0][0] else CASES[0]
    a = gw.build_record(product_id=pid, functions=fn, ui=ui, layer2=_l2())
    b = gw.build_record(product_id=other[0], functions=other[1], ui=other[2], layer2=_l2())
    assert a != b and a["layer1"]["functions"]["functions"] != b["layer1"]["functions"]["functions"]


@pytest.mark.parametrize("fn,ui,missing", [
    (None, _ui(), "function grader did not run"),
    (_fn(), None, "ui grader did not run"),
    ("not a grade", _ui(), "function grader did not run"),
])
def test_missing_grader_is_written_red_not_dropped(tmp_path, fn, ui, missing):
    g = gw.write_build_grade(tmp_path, product_id="p", functions=fn, ui=ui, layer2=_l2())
    assert g["layer1"]["ok"] is False and g["certified"] is False
    part = g["layer1"]["functions"] if missing.startswith("function") else g["layer1"]["ui"]
    assert part["failing"] == [missing]
    assert "## Layer 1 (deterministic): RED" in (tmp_path / "GRADE.md").read_text()


@pytest.mark.parametrize("fn,ui", [(_fn(ok=False), _ui()), (_fn(), _ui(ok=False)), (dict(_fn(), ok="yes"), _ui())])
def test_red_layer1_never_certified(fn, ui):
    g = gw.build_record(product_id="p", functions=fn, ui=ui, layer2=_l2())
    assert g["layer1"]["ok"] is False and g["certified"] is False


@pytest.mark.parametrize("l2,open_", [
    (_l2({"question": "q2", "verdict": "FAIL", "evidence": EV}), ["q2"]),
    (_l2({"question": "q3", "verdict": "pass", "evidence": EV}), ["q3"]),
    (_l2({"question": "q4", "verdict": "PASS", "evidence": "  "}), ["q4"]),
    (_l2(open_fails=["q5"]), ["q5"]),
    (None, ["layer 2 did not run"]),
])
def test_layer2_blocks_and_names_next_tasks(l2, open_):
    g = gw.build_record(product_id="p", functions=_fn(), ui=_ui(), layer2=l2)
    assert g["certified"] is False and g["layer2"]["open_fails"] == open_
    assert g["next_tasks"] == [f"re-run writer on: {q}" for q in open_]


@pytest.mark.parametrize("l2", [_l2(ran=False), {"ran": "true", "verdicts": [{"question": "q", "verdict": "PASS", "evidence": EV}]},
                                {"ran": True, "verdicts": []}])
def test_layer2_not_run_or_empty_not_certified(l2):
    assert gw.build_record(product_id="p", functions=_fn(), ui=_ui(), layer2=l2)["certified"] is False


@pytest.mark.parametrize("pid", ["", "   ", None])
def test_product_id_required(tmp_path, pid):
    with pytest.raises(ValueError):
        gw.write_build_grade(tmp_path, product_id=pid, functions=_fn(), ui=_ui(), layer2=_l2())
    assert not (tmp_path / "grade.json").exists()


@pytest.mark.parametrize("first,second", [(True, False), (False, True)])
def test_rewrite_replaces_previous_grade(tmp_path, first, second):
    gw.write_build_grade(tmp_path, product_id="p", functions=_fn(ok=first), ui=_ui(), layer2=_l2())
    gw.write_build_grade(tmp_path, product_id="p", functions=_fn(ok=second), ui=_ui(), layer2=_l2())
    assert json.loads((tmp_path / "grade.json").read_text())["certified"] is second


# --- G2 #79 repairs: M1 non-string evidence; M2-M8 render_md directly -----
@pytest.mark.parametrize("ev", [5, ["quote"], {"q": "x"}, None, True, "   "])
def test_non_string_evidence_is_rejected(ev):
    g = gw.build_record(product_id="p", functions=_fn(), ui=_ui(),
                        layer2=_l2({"question": "qx", "verdict": "PASS", "evidence": ev}))
    assert g["layer2"]["open_fails"] == ["qx"] and g["certified"] is False
    assert all(isinstance(v["evidence"], str) for v in g["layer2"]["verdicts"])


def _md(**kw):
    args = {"product_id": "p", "functions": _fn(), "ui": _ui(), "layer2": _l2()}
    args.update(kw)
    return gw.render_md(gw.build_record(**args))


@pytest.mark.parametrize("pid", ["lettings-desk", "site-ops"])
def test_md_title_names_the_product(pid):
    assert _md(product_id=pid).splitlines()[0] == f"# GRADE -- {pid}"


@pytest.mark.parametrize("kw,cert,l1", [
    ({}, "YES", "GREEN"),
    ({"functions": _fn(ok=False)}, "NO", "RED"),
    ({"ui": _ui(ok=False)}, "NO", "RED"),
    ({"layer2": _l2(ran=False)}, "NO", "GREEN"),
])
def test_md_certified_and_layer1_lines(kw, cert, l1):
    md = _md(**kw)
    assert f"**Certified:** {cert} " in md and f"## Layer 1 (deterministic): {l1}" in md


@pytest.mark.parametrize("ids", [("a", "b", "c", "d"), ("w", "x", "y", "z", "v")])
def test_md_lists_every_function_with_mutants(ids):
    md = _md(functions=_fn(ids=ids))
    assert f"- Functions graded: {len(ids)} (min 4)" in md
    for c in ids:
        assert f"  - `{c}`: PASS; mutants killed constant_return, drop_rule; survived none" in md


@pytest.mark.parametrize("ids", [("a", "b", "c", "d"), ("q", "r", "s", "t")])
def test_md_failing_function_lines(ids):
    md = _md(functions=_fn(ok=False, ids=ids))
    for c in ids:
        assert f"  - `{c}`: FAIL; mutants killed none; survived drop_rule" in md and f"  - FAIL: {c}" in md


@pytest.mark.parametrize("names,ok,want", [(("load:/", "route:/a"), True, "2/2"), (("load:/", "s:1", "s:2"), False, "0/3")])
def test_md_ui_step_count(names, ok, want):
    md = _md(ui=_ui(ok=ok, names=names))
    assert f"- UI steps passed: {want}" in md
    assert all((f"  - FAIL: {n}" in md) is (not ok) for n in names)


@pytest.mark.parametrize("ran,word", [(True, "ran"), (False, "did not run")])
def test_md_layer2_header(ran, word):
    assert f"## Layer 2 (separate grader context): {word}\n" in _md(layer2=_l2(ran=ran))


@pytest.mark.parametrize("verdict,ev", [("PASS", EV), ("FAIL", "button missing")])
def test_md_verdict_line_and_open(verdict, ev):
    md = _md(layer2=_l2({"question": "does it list rents", "verdict": verdict, "evidence": ev}))
    assert f'- {verdict}: does it list rents -- evidence: "{ev}"' in md
    assert ("- OPEN: does it list rents" in md) is (verdict != "PASS")
    assert ("## Next tasks\n- re-run writer on: does it list rents" in md) is (verdict != "PASS")


@pytest.mark.parametrize("missing", ["functions", "ui"])
def test_md_skipped_grader_is_shown_red(missing):
    md = _md(**{missing: None})
    assert "## Layer 1 (deterministic): RED" in md and f"  - FAIL: {'function' if missing == 'functions' else 'ui'} grader did not run" in md


@pytest.mark.parametrize("pid", ["a1", "b2"])
def test_write_grade_json_roundtrips_and_is_atomic(tmp_path, pid):
    g = gw.build_record(product_id=pid, functions=_fn(), ui=_ui(), layer2=_l2())
    p = gw.write_grade_json(tmp_path / "out", g)
    assert p == tmp_path / "out" / "grade.json" and json.loads(p.read_text()) == g
    assert not (tmp_path / "out" / "grade.json.tmp").exists()


# --- G2 M7: each function's ok must be exactly True to render PASS --------
@pytest.mark.parametrize("ok", ["yes", 1, "true", ["x"]])
def test_truthy_function_ok_renders_fail(ok):
    fn = _fn()
    fn["functions"][0]["ok"] = ok
    cid = fn["functions"][0]["capability_id"]
    md = gw.render_md(gw.build_record(product_id="p", functions=fn, ui=_ui(), layer2=_l2()))
    assert f"  - `{cid}`: FAIL;" in md and f"  - `{cid}`: PASS;" not in md
    assert all(f"  - `{f['capability_id']}`: PASS;" in md for f in fn["functions"][1:])
