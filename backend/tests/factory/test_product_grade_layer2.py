"""F-8c-2: Layer 2 message shape -- separate context, three inputs only."""

from __future__ import annotations

import json

import pytest

from app.factory.build.product_grader import layer2 as l2

BPS = [
    {"capabilities": [{"id": "rent_roll", "description": "Roll rent."}], "ui_modules": ["portal"]},
    {"capabilities": [{"id": "x", "description": "X."}, {"id": "y", "description": "Y."}], "ui_modules": ["map", "board"]},
]
INPUTS = [
    {"compiled_brief": "brief A", "rendered_product": "<html>A</html>", "layer1": {"ok": True}},
    {"compiled_brief": "brief B"},
]


@pytest.mark.parametrize("inputs", INPUTS)
@pytest.mark.parametrize("bp", BPS)
def test_message_shape(inputs, bp):  # G23
    qs = l2.layer2_questions(bp)
    msgs = l2.layer2_messages(inputs, qs)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == l2.GRADER_SYSTEM_PROMPT
    body = json.loads(msgs[1]["content"])
    assert sorted(body) == sorted([*l2.LAYER2_INPUTS, "questions"])
    assert body["questions"] == qs
    assert {k: body[k] for k in l2.LAYER2_INPUTS} == {k: inputs.get(k) for k in l2.LAYER2_INPUTS}


@pytest.mark.parametrize("key", ["writer_prompt", "writer_transcript", "messages", "notes", "history"])
def test_any_other_input_is_refused(key):  # G24
    with pytest.raises(l2.ContextLeak, match=key):
        l2.layer2_messages({"compiled_brief": "b", key: "x"}, ["q"])


@pytest.mark.parametrize("bp", BPS)
def test_run_layer2_sends_exactly_the_built_messages_once(bp):  # G25
    calls = []

    def complete(msgs):
        calls.append(msgs)
        return "[]"

    l2.run_layer2(INPUTS[0], bp, complete)
    assert calls == [l2.layer2_messages(INPUTS[0], l2.layer2_questions(bp))]


@pytest.mark.parametrize("bp", BPS)
def test_context_leak_propagates_before_any_call(bp):
    calls = []
    with pytest.raises(l2.ContextLeak):
        l2.run_layer2({"writer_prompt": "x"}, bp, lambda m: calls.append(m) or "[]")
    assert calls == []


@pytest.mark.parametrize("qs", [[], ()])
def test_no_questions_is_an_error(qs):
    with pytest.raises(ValueError):
        l2.layer2_messages({}, qs)


@pytest.mark.parametrize("bp", BPS)
def test_questions_cover_capabilities_screens_and_grounding(bp):
    qs = l2.layer2_questions(bp)
    assert len(qs) == len(bp["capabilities"]) + len(bp["ui_modules"]) + 1
    for m in bp["ui_modules"]:
        assert sum(f"'{m}'" in q for q in qs) == 1
    assert qs[-1].startswith("Is every number the product shows grounded")


@pytest.mark.parametrize("raw,reason", [("not json", "JSONDecodeError"), ("{}", "not a JSON list"), ('"x"', "not a JSON list")])
def test_no_answer_is_recorded_not_run(raw, reason):
    r = l2.run_layer2(INPUTS[1], BPS[0], lambda m: raw)
    assert r["ran"] is False and r["verdicts"] == [] and reason in r["open_fails"][0]


@pytest.mark.parametrize("bp", BPS)
def test_unanswered_questions_are_open(bp):
    qs = l2.layer2_questions(bp)
    raw = json.dumps([{"question": qs[0], "verdict": "PASS", "evidence": "e" * 900}, "junk"])
    r = l2.run_layer2(INPUTS[1], bp, lambda m: raw)
    assert r["open_fails"] == qs[1:]
    assert r["verdicts"] == [{"question": qs[0], "verdict": "PASS", "evidence": "e" * l2.EVIDENCE_MAX}]


# --- G2 L6: a successful Layer-2 run records ran True (and only it does) ---
_L6_BPS = [
    {"capabilities": [{"id": "rent_roll", "description": "Roll rent."}], "ui_modules": ["tenant_portal"]},
    {"capabilities": [{"id": "defect_raise", "description": "Raise defects."}, {"id": "crew_roster", "description": "Crew."}]},
]


@pytest.mark.parametrize("bp", _L6_BPS)
def test_successful_run_records_ran_true(bp):
    from app.factory.build.product_grader import layer2 as l2m

    qs = l2m.layer2_questions(bp)
    answer = json.dumps([{"question": q, "verdict": "PASS", "evidence": f'"{q[:20]}"'} for q in qs])
    out = l2m.run_layer2({"compiled_brief": "b", "rendered_product": "p", "layer1": {}}, bp, lambda m: answer)
    assert out["ran"] is True and out["open_fails"] == [] and [v["question"] for v in out["verdicts"]] == qs


@pytest.mark.parametrize("reply,ran", [("not json", False), ('{"a": 1}', False), ("[]", True)])
def test_ran_is_true_exactly_when_the_grader_answered_a_list(reply, ran):
    from app.factory.build.product_grader import layer2 as l2m

    out = l2m.run_layer2({"compiled_brief": "b"}, _L6_BPS[0], lambda m: reply)
    assert out["ran"] is ran and out["open_fails"]


# --- G2 X10: the recorded verdict is the grader's verdict, FAIL included ---
@pytest.mark.parametrize("verdicts", [["FAIL"], ["PASS", "FAIL"], ["FAIL", "PASS", "FAIL"]])
def test_recorded_verdict_is_the_graders_verdict(verdicts):
    bp = {"capabilities": [{"id": f"c{i}", "description": f"D{i}."} for i in range(len(verdicts) - 1)]}
    qs = l2.layer2_questions(bp)
    raw = json.dumps([{"question": q, "verdict": v, "evidence": "e"} for q, v in zip(qs, verdicts)])
    out = l2.run_layer2(INPUTS[0], bp, lambda m: raw)
    assert [v["verdict"] for v in out["verdicts"]] == verdicts


# --- G2 X8: each capability question carries the capability's description ---
@pytest.mark.parametrize("caps", [
    [{"id": "rent_roll", "description": "Roll the monthly rent ledger."}],
    [{"id": "a", "description": "Raise a site defect with photo."}, {"id": "b", "description": "Show the crew roster by day."}],
])
def test_capability_question_carries_its_description(caps):
    qs = l2.layer2_questions({"capabilities": caps})
    for c, q in zip(caps, qs):
        assert f"'{c['id']}'" in q and c["description"] in q
