"""F-8c: grade.json/GRADE.md carry both layers; only Layer 1 certifies;
the Builds gate refuses a build with no grade, a red Layer 1, or an open
Layer-2 FAIL."""

from __future__ import annotations

import json

import pytest

from app.factory.build.product_grader import grade_record as gr

BPS = [
    {"capabilities": [{"id": "rent_roll", "description": "roll rent"}], "ui_modules": ["portal"]},
    {"capabilities": [{"id": "defect_register", "description": "raise defects"}, {"id": "crew", "description": "assign crews"}]},
]
GREEN_FN = {"ok": True, "graded": 5, "min_functions": 4, "functions": []}
GREEN_UI = {"ok": True, "steps": [{"name": "load:/", "passed": True}], "failing": []}
RED_FN = dict(GREEN_FN, ok=False)
RED_UI = dict(GREEN_UI, ok=False, failing=["load:/"])


def _passing(bp):
    return lambda msgs: json.dumps([{"question": q, "verdict": "PASS", "evidence": "quoted"} for q in gr.layer2_questions(bp)])


@pytest.mark.parametrize("bp", BPS)
def test_green_both_layers_certifies(tmp_path, bp):
    l2 = gr.run_layer2({"compiled_brief": "b", "rendered_product": "p", "layer1": {}}, bp, _passing(bp))
    g = gr.build_grade(product_id="p", functions=GREEN_FN, ui=GREEN_UI, layer2=l2)
    gr.write_grade(tmp_path, g)
    assert g["certified"] and gr.certification_refusal(tmp_path) is None
    assert "Certified:** YES" in (tmp_path / gr.GRADE_MD).read_text()


@pytest.mark.parametrize("fn,ui", [(RED_FN, GREEN_UI), (GREEN_FN, RED_UI)])
def test_layer2_pass_never_certifies_red_layer1(tmp_path, fn, ui):
    bp = BPS[0]
    l2 = gr.run_layer2({"compiled_brief": "b", "rendered_product": "p", "layer1": {}}, bp, _passing(bp))
    g = gr.build_grade(product_id="p", functions=fn, ui=ui, layer2=l2)
    gr.write_grade(tmp_path, g)
    assert not g["certified"]
    assert "Layer 1 RED" in gr.certification_refusal(tmp_path)


def test_hand_set_certified_over_red_layer1_is_refused(tmp_path):
    g = gr.build_grade(product_id="p", functions=RED_FN, ui=GREEN_UI, layer2={"ran": True, "open_fails": []})
    g["certified"] = True
    gr.write_grade(tmp_path, g)
    assert gr.certification_refusal(tmp_path)


@pytest.mark.parametrize("bp", BPS)
def test_layer2_fail_blocks_and_is_a_named_task(tmp_path, bp):
    qs = gr.layer2_questions(bp)
    l2 = gr.run_layer2({"compiled_brief": "b"}, bp, lambda m: json.dumps([{"question": qs[0], "verdict": "FAIL", "evidence": "x"}]))
    g = gr.build_grade(product_id="p", functions=GREEN_FN, ui=GREEN_UI, layer2=l2)
    gr.write_grade(tmp_path, g)
    assert not g["certified"] and g["next_tasks"][0].endswith(qs[0])
    assert "open Layer-2" in gr.certification_refusal(tmp_path)


@pytest.mark.parametrize("leak", ["writer_transcript", "writer_prompt"])
def test_layer2_given_writer_context_is_refused(leak):
    with pytest.raises(gr.ContextLeak):
        gr.run_layer2({"compiled_brief": "b", leak: "..."}, BPS[0], lambda m: "[]")


@pytest.mark.parametrize("raw", ["not json", "{}"])
def test_layer2_no_answer_is_open_fail(raw):
    l2 = gr.run_layer2({"compiled_brief": "b"}, BPS[1], lambda m: raw)
    assert l2["open_fails"] and not l2["ran"]


@pytest.mark.parametrize("bp", BPS)
def test_two_blueprints_two_question_sets(bp):
    other = BPS[1] if bp is BPS[0] else BPS[0]
    assert gr.layer2_questions(bp) != gr.layer2_questions(other)


def test_no_grade_is_refused(tmp_path):
    assert "grade.json missing" in gr.certification_refusal(tmp_path)


def test_store_gate_success_refuses_ungraded_build(tmp_path):
    from app.factory.build import n3_store_gate as n3
    from app.factory.build.builds_push import BuildsPushError

    snap = n3.StoreGateSnapshot(
        state="success", passed=n3.ACCEPTANCE_REQUIRED, total=n3.ACCEPTANCE_REQUIRED,
        ok=True, missing=False, sha="a" * 40, branch="build/x", owner="o", repo="r",
    )
    assert snap.is_12_of_12
    with pytest.raises(BuildsPushError, match="grade"):
        n3.apply_store_gate_success(tmp_path, snap)
