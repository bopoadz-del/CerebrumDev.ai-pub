"""FG-5: one derivation of what is open in a grade.

The tier, the writer's work list, the reason classes, the record and the
Store gate all read ``grade_record.open_layer2`` (and the one hardwiring
rule, ``grade_record.hardwiring_ok``), so they cannot disagree. Every test
is paired: the same input with a PASS / clean control and with a FAIL /
finding. Nothing here names a product, a client or a capability: the
fixtures are the invented ones of test_a_grader.
"""

from __future__ import annotations

import json

import pytest

import app.factory.build.grader as grader
import app.factory.build.product_grader.grade_record as gr
from app.factory.build.product_grader.layer2 import layer2_questions
from tests.factory.test_a_grader import IDS, READS_INPUT, _bp, _workspace, green_ui, independent_grader  # noqa: F401

GREEN_FN = {"ok": True, "graded": 5}
GREEN_UI = {"ok": True, "steps": []}


def _answer_all(verdict):
    """A Layer-2 completion that answers every question it is sent, by id,
    with ``verdict`` and evidence quoting that question's own id."""

    def complete(messages):
        questions = json.loads(messages[-1]["content"])["questions"]
        return json.dumps(
            [{"id": q["id"], "verdict": verdict, "evidence": f"recorded for {q['id']}: quoted"} for q in questions]
        )

    return complete


@pytest.mark.parametrize("verdict", ["FAIL", "PASS"])
def test_an_answered_layer2_fail_blocks_the_in_build_tier_and_reaches_the_writer(
    tmp_path, independent_grader, green_ui, monkeypatch, verdict  # noqa: F811
):
    monkeypatch.setattr(grader, "_grader_complete", _answer_all(verdict))
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS)
    out = grader.run_grader(plan_capabilities=IDS, blueprint=bp, workspace=ws, product_id="invented-product")
    record = json.loads((ws / "grade.json").read_text("utf-8"))
    texts = [q["text"] for q in layer2_questions(bp)]
    handed = [i for i in out.writer_items if i.startswith("re-run writer on: ")]
    l2_reasons = [r for r in out.reasons if r["class"] == "layer2_fail"]
    if verdict == "FAIL":
        assert out.tier == grader.NOT_CERTIFIED
        assert len(handed) == len(texts)
        for text in texts:
            assert any(text in i and "grader FAIL" in i and "quoted" in i for i in handed), (text, handed)
        assert len(l2_reasons) == len(texts)
        assert record["certified"] is False
        assert record["layer2"]["open_fails"] == texts
        assert gr.certification_refusal(ws) == f"product grade has {len(texts)} open Layer-2 FAIL(s): handover blocked"
    else:
        assert out.tier == grader.CERTIFIED
        assert handed == [] and l2_reasons == []
        assert record["certified"] is True
        assert gr.certification_refusal(ws) is None


@pytest.mark.parametrize("evidence,open_", [("", True), ("  \n", True), ('the page says "total 12"', False)])
def test_a_pass_without_quoted_evidence_is_an_open_item(evidence, open_):
    l2 = {"ran": True, "verdicts": [{"question": "q1", "verdict": "PASS", "evidence": evidence}], "open_fails": []}
    g = gr.build_grade(product_id="p", functions=GREEN_FN, ui=GREEN_UI, layer2=l2)
    assert (gr.open_layer2(g["layer2"]) != []) is open_
    assert (g["layer2"]["open_fails"] == ["q1"]) is open_
    assert g["certified"] is (not open_)
    assert (g["next_tasks"] == [f're-run writer on: q1 -- grader PASS: "{evidence}"']) is open_


@pytest.mark.parametrize("evidence", [None, ["a quoted line"], {"quote": "x"}, 7])
def test_non_string_evidence_gives_a_readable_record_with_that_question_open(tmp_path, evidence):
    l2 = {
        "ran": True,
        "verdicts": [
            {"question": "q1", "verdict": "PASS", "evidence": evidence},
            {"question": "q2", "verdict": "PASS", "evidence": "quoted"},
        ],
        "open_fails": [],
    }
    g = gr.build_grade(product_id="p", functions=GREEN_FN, ui=GREEN_UI, layer2=l2)
    assert gr._schema_errors(g) == []
    assert g["layer2"]["open_fails"] == ["q1"]
    gr.write_grade_json(tmp_path, g)
    assert gr.certification_refusal(tmp_path) == "product grade has 1 open Layer-2 FAIL(s): handover blocked"


@pytest.mark.parametrize("raw,open_", [("not a verdict", True), ({"question": "q1", "verdict": "PASS", "evidence": "quoted"}, False)])
def test_normalise_keeps_a_non_verdict_open(raw, open_):
    rows = gr.normalise_verdicts([raw])
    assert len(rows) == 1 and all(isinstance(rows[0][k], str) for k in ("question", "verdict", "evidence"))
    assert (gr.open_layer2({"verdicts": [raw], "open_fails": []}) != []) is open_


@pytest.mark.parametrize("recorded", [["q1"], ["q1", "q3"]])
def test_open_layer2_lists_a_question_once_with_its_evidence_first(recorded):
    l2 = {"verdicts": [{"question": "q1", "verdict": "FAIL", "evidence": "said x"}], "open_fails": recorded}
    rows = gr.open_layer2(l2)
    assert rows[0] == {"question": "q1", "verdict": "FAIL", "evidence": "said x"}
    assert [r["question"] for r in rows] == list(dict.fromkeys(recorded))
    assert gr.open_fails(l2) == [r["question"] for r in rows]
    assert gr.layer2_task(rows[0]) == 're-run writer on: q1 -- grader FAIL: "said x"'
    assert all(gr.layer2_task(r) == f"re-run writer on: {r['question']}" for r in rows[1:])


def _hardwiring(clean):
    findings = [] if clean else [{"kind": "forbidden_literal", "file": "app/x.py", "literals": ["v"]}]
    return {"ran": True, "clean": clean, "findings": findings}


@pytest.mark.parametrize("clean", [True, False])
def test_the_store_gate_applies_the_records_hardwiring_rule(tmp_path, clean):
    l2 = {"ran": True, "verdicts": [{"question": "q1", "verdict": "PASS", "evidence": "quoted"}], "open_fails": []}
    g = gr.build_grade(product_id="p", functions=GREEN_FN, ui=GREEN_UI, layer2=l2, hardwiring=_hardwiring(clean))
    # Write the record as a hand-edited one would be: certified claimed true.
    gr.write_grade_json(tmp_path, dict(g, certified=True))
    refusal = gr.certification_refusal(tmp_path)
    assert g["certified"] is clean
    assert gr.hardwiring_ok(g["hardwiring"]) is clean
    if clean:
        assert refusal is None
    else:
        assert refusal == "product grade hardwiring scan not clean (1 finding(s)): build not certified"


@pytest.mark.parametrize("hardwiring,ok", [
    (None, True),
    ({"ran": True, "clean": True, "findings": []}, True),
    ({"ran": False, "clean": True, "findings": []}, False),
    ({"ran": True, "clean": False, "findings": []}, False),
])
def test_hardwiring_ok_is_one_rule(hardwiring, ok):
    assert gr.hardwiring_ok(hardwiring) is ok
