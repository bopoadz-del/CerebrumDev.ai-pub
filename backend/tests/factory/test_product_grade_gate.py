"""F-8c-1: only Layer 1 certifies; Layer 2 can only block; the Builds gate
re-derives the decision from grade.json and refuses on anything doubtful."""

from __future__ import annotations

import json

import pytest

from app.factory.build.product_grader import grade_record as gr

GREEN_FN = {"ok": True, "graded": 5}
GREEN_UI = {"ok": True, "steps": []}
EV = 'the page says "rent roll total 1,240"'


def _l2(*verdicts, ran=True, open_fails=()):
    return {"ran": ran, "verdicts": list(verdicts), "open_fails": list(open_fails)}


def _v(verdict="PASS", evidence=EV, q="q1"):
    return {"question": q, "verdict": verdict, "evidence": evidence}


def _write(tmp_path, **kw):
    g = gr.build_grade(product_id="p", functions=kw.get("fn", GREEN_FN), ui=kw.get("ui", GREEN_UI), layer2=kw.get("l2", _l2(_v())))
    gr.write_grade_json(tmp_path, g)
    return g


@pytest.mark.parametrize("verdict,ok", [
    (_v(), True),
    (_v(q="other"), True),
    (_v("FAIL"), False),                 # H4: a recorded FAIL is never accepted
    (_v("FAIL", "quoted but failing"), False),
    (_v(evidence=""), False),
    (_v(evidence="   \n\t"), False),     # H5: whitespace is not evidence
    (_v("pass"), False),                 # H6: case-exact PASS only
    (_v("Pass"), False),
    (_v(" PASS"), False),
    (_v(evidence=None), False),
    (_v(evidence=["x"]), False),
    ("PASS", False),
    (None, False),
])
def test_is_pass(verdict, ok):
    assert gr.is_pass(verdict) is ok


@pytest.mark.parametrize("bad", [_v("FAIL"), _v(evidence="  "), _v("pass")])
def test_build_grade_and_gate_refuse_non_pass_verdicts(tmp_path, bad):
    g = _write(tmp_path, l2=_l2(_v(), dict(bad, question="q2")))
    assert g["certified"] is False and g["layer2"]["open_fails"] == ["q2"]
    assert g["next_tasks"] == ["re-run writer on: q2"]
    assert gr.certification_refusal(tmp_path) == "product grade has 1 open Layer-2 FAIL(s): handover blocked"


@pytest.mark.parametrize("bad", [_v("FAIL"), _v(evidence="  "), _v("pass")])
def test_gate_rederives_from_verdicts_not_recorded_open_fails(tmp_path, bad):
    g = _write(tmp_path)
    g["layer2"]["verdicts"].append(dict(bad, question="q9"))
    g["certified"] = True
    gr.write_grade_json(tmp_path, g)
    assert "1 open Layer-2" in gr.certification_refusal(tmp_path)


@pytest.mark.parametrize("fn,ui,certified", [
    (GREEN_FN, GREEN_UI, True),
    ({"ok": False}, GREEN_UI, False),
    (GREEN_FN, {"ok": False}, False),
    ({"ok": "yes"}, GREEN_UI, False),    # G33: only a literal True is green
    (GREEN_FN, {}, False),
])
def test_layer1_alone_certifies(tmp_path, fn, ui, certified):  # G33
    g = _write(tmp_path, fn=fn, ui=ui)
    assert g["certified"] is certified and g["layer1"]["ok"] is certified
    assert (gr.certification_refusal(tmp_path) is None) is certified


@pytest.mark.parametrize("l2,reason", [
    (_l2(_v(), ran=False), "Layer 2 did not run"),     # G34
    ({"verdicts": [_v()]}, "unreadable"),            # no ran: off-schema (S1), still refused
    (_l2(_v(), ran="true"), "unreadable"),
    (_l2(), "no verdicts"),
    (_l2(_v(), open_fails=["q7"]), "1 open Layer-2"),
])
def test_layer2_can_only_block(tmp_path, l2, reason):  # G34
    g = _write(tmp_path, l2=l2)
    assert g["certified"] is False
    assert reason in gr.certification_refusal(tmp_path)


def test_hand_set_certified_is_not_trusted(tmp_path):
    g = _write(tmp_path, fn={"ok": False})
    g["certified"] = True
    gr.write_grade_json(tmp_path, g)
    assert "Layer 1 RED" in gr.certification_refusal(tmp_path)


@pytest.mark.parametrize("content", [
    "{not json", "[]", "{}", '"str"', "", '{"schema": "product_grade.v1"}',
    json.dumps({"schema": "other", "layer1": {}, "layer2": {}}),
    json.dumps({"schema": gr.SCHEMA, "layer1": [], "layer2": {}}),
    json.dumps({"schema": gr.SCHEMA, "layer1": {"functions": {}, "ui": {}}, "layer2": {"verdicts": "PASS"}}),
])
def test_unreadable_grade_refuses(tmp_path, content):
    (tmp_path / gr.GRADE_JSON).write_text(content)
    assert gr.certification_refusal(tmp_path) == "product grade unreadable: build not certified"


@pytest.mark.parametrize("raw", [b"\xff\xfe\x00", b"\x80abc"])
def test_undecodable_grade_refuses(tmp_path, raw):
    (tmp_path / gr.GRADE_JSON).write_bytes(raw)
    assert gr.certification_refusal(tmp_path) == "product grade unreadable: build not certified"


@pytest.mark.parametrize("sub", ["", "nested/dir"])
def test_no_grade_is_refused(tmp_path, sub):
    assert gr.certification_refusal(tmp_path / sub) == "no product grade (grade.json missing): build not certified"


def test_store_gate_success_refuses_ungraded_build(tmp_path):
    from app.factory.build import n3_store_gate as n3
    from app.factory.build.builds_push import BuildsPushError

    snap = n3.StoreGateSnapshot(
        state="success", passed=n3.ACCEPTANCE_REQUIRED, total=n3.ACCEPTANCE_REQUIRED,
        ok=True, missing=False, sha="a" * 40, branch="build/x", owner="o", repo="r",
    )
    with pytest.raises(BuildsPushError, match="grade.json missing"):
        n3.apply_store_gate_success(tmp_path, snap)


# --- G2 #75 repairs: S1 _load enforces the schema; one writer -------------
from app.factory.build.product_grader import grade_writer as gw  # noqa: E402


def test_gate_uses_the_writers_write_grade_json():
    assert gr.write_grade_json is gw.write_grade_json


def _good():
    return gr.build_grade(product_id="p", functions=GREEN_FN, ui=GREEN_UI, layer2=_l2(_v()))


def _set(path, value):
    def m(g):
        *head, last = path
        for k in head:
            g = g[k]
        if value is KeyError:
            del g[last]
        else:
            g[last] = value
    return m


S1 = [
    _set(["product_id"], ""), _set(["product_id"], 7), _set(["product_id"], KeyError),
    _set(["certified"], "yes"), _set(["certified"], KeyError),
    _set(["next_tasks"], "x"), _set(["next_tasks"], [1]),
    _set(["layer1", "ok"], "true"), _set(["layer1", "ok"], 1),
    _set(["layer1", "functions"], []), _set(["layer1", "ui", "ok"], "yes"), _set(["layer1", "functions", "ok"], KeyError),
    _set(["layer2", "ran"], "yes"), _set(["layer2", "ran"], KeyError),
    _set(["layer2", "verdicts"], "PASS"), _set(["layer2", "verdicts"], [{"question": "q", "verdict": "PASS"}]),
    _set(["layer2", "verdicts"], [{"question": "q", "verdict": "PASS", "evidence": 5}]),
    _set(["layer2", "verdicts"], ["PASS"]),
    _set(["layer2", "open_fails"], "none"), _set(["layer2", "open_fails"], [None]),
    _set(["schema"], "product_grade.v2"), _set(["extra"], 1),
]


@pytest.mark.parametrize("break_it", S1)
def test_load_enforces_schema(tmp_path, break_it):
    g = _good()
    assert gr.certification_refusal(_written(tmp_path / "ok", g)) is None
    break_it(g)
    assert gr.certification_refusal(_written(tmp_path / "bad", g)) == "product grade unreadable: build not certified"


def _written(root, g):
    root.mkdir(parents=True, exist_ok=True)
    (root / "grade.json").write_text(json.dumps(g))
    return root


@pytest.mark.parametrize("pid", ["a", "lettings-desk"])
def test_schema_errors_empty_for_writer_output(pid):
    g = gw.build_record(product_id=pid, functions={"ok": True}, ui={"ok": True},
                        layer2={"ran": True, "verdicts": [{"question": "q", "verdict": "PASS", "evidence": EV}]})
    assert gr._schema_errors(g) == [] and gr._schema_errors(json.loads(json.dumps(g))) == []
