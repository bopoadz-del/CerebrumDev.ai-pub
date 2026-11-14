"""F-G3: Layer 2 grades according to the prompt.

Questions carry ids and ask the requirements the writer was given; the
answer is matched by id (never by echoing the question text); each
capability is asked in its own call with its own files rendered first and
whole. Every case runs on two inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.factory.build.grader as grader
import app.factory.build.product_grader.layer2 as l2
from app.factory.build.product_grader import grade_record as gr

INPUTS = {"compiled_brief": "brief", "rendered_product": "plain render", "layer1": {"ok": True}}

BPS = [
    {
        "capabilities": [
            {"id": "ledger-close", "description": "Close the month.", "requirements": ["sum each line", "lock the period"]},
            {"id": "crew_plan", "description": "Plan the crew."},
        ],
        "ui_modules": ["board"],
    },
    {
        "capabilities": [
            {"id": "intake", "description": "Take an intake.", "requirements": ["  reject a blank owner  ", "", "store the amount"]},
        ],
        "ui_modules": [],
    },
]


def _sent(messages):
    return json.loads(messages[-1]["content"])["questions"]


def _answer_all(verdict="PASS", evidence='the page says "total 12"', text=None):
    """A completion that answers every question it is sent, by id; ``text``
    replaces the echoed question (paraphrase) or drops it (None)."""

    def complete(messages):
        out = []
        for q in _sent(messages):
            item = {"id": q["id"], "verdict": verdict, "evidence": evidence}
            if text is not None:
                item["question"] = text(q["text"])
            out.append(item)
        return json.dumps(out)

    return complete


def _record(out):
    return gr.open_fails(out)


# --- the pilot defect: a PASS answered by id with a paraphrased question ---
@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("text", [None, lambda t: t.lower().rstrip("?") + " (paraphrased)"])
def test_a_pass_answered_by_id_is_a_pass_whatever_the_echo(bp, text):
    out = l2.run_layer2(INPUTS, bp, _answer_all(text=text))
    qs = l2.layer2_questions(bp)
    assert out["ran"] is True
    assert out["open_fails"] == []
    assert [v["question"] for v in out["verdicts"]] == [q["text"] for q in qs]
    assert all(v["verdict"] == "PASS" for v in out["verdicts"])
    assert _record(out) == []


# --- unknown and repeated ids are not answers ---
@pytest.mark.parametrize("bp", BPS)
def test_an_unknown_id_is_not_an_answer(bp):
    def complete(messages):
        return json.dumps([{"id": q["id"] + "x", "verdict": "PASS", "evidence": "e"} for q in _sent(messages)])

    out = l2.run_layer2(INPUTS, bp, complete)
    assert out["verdicts"] == []
    assert out["open_fails"] == [q["text"] for q in l2.layer2_questions(bp)]


@pytest.mark.parametrize("bp", BPS)
def test_a_repeated_id_answers_once_only(bp):
    def complete(messages):
        qs = _sent(messages)
        first = qs[0]["id"]
        return json.dumps([
            {"id": first, "verdict": "FAIL", "evidence": "first"},
            {"id": first, "verdict": "PASS", "evidence": "second"},
        ])

    out = l2.run_layer2(INPUTS, bp, complete)
    assert [v["evidence"] for v in out["verdicts"]] == ["first"] * len(out["verdicts"])
    assert all(v["verdict"] == "FAIL" for v in out["verdicts"])
    qs = l2.layer2_questions(bp)
    tail = [q for q in qs if not q["capability"]]
    assert out["open_fails"] == [q["text"] for q in tail[1:]]


# --- a FAIL stays a FAIL; a PASS without quoted evidence is not a pass ---
@pytest.mark.parametrize("bp", BPS)
def test_a_fail_verdict_stays_a_fail(bp, tmp_path):
    out = l2.run_layer2(INPUTS, bp, _answer_all(verdict="FAIL"))
    qs = l2.layer2_questions(bp)
    assert out["open_fails"] == []
    assert _record(out) == [q["text"] for q in qs]
    g = gr.build_grade(product_id="p", functions={"ok": True, "graded": 5}, ui={"ok": True, "steps": []}, layer2=out)
    gr.write_grade_json(tmp_path, g)
    assert gr.certification_refusal(tmp_path) is not None


@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("evidence", ["", "   "])
def test_a_pass_without_quoted_evidence_is_not_a_pass(bp, evidence):
    out = l2.run_layer2(INPUTS, bp, _answer_all(evidence=evidence))
    assert _record(out) == [q["text"] for q in l2.layer2_questions(bp)]


# --- the writer's requirements are asked verbatim; else the description ---
@pytest.mark.parametrize("bp", BPS)
def test_requirements_lines_appear_verbatim_in_the_question(bp):
    qs = l2.layer2_questions(bp)
    for c, q in zip(bp["capabilities"], qs):
        assert q["capability"] == c["id"]
        reqs = [r.strip() for r in c.get("requirements") or [] if r.strip()]
        assert c["description"] in q["text"]
        if reqs:
            assert " | ".join(reqs) in q["text"]
        else:
            assert "Requirements:" not in q["text"]


# --- the capability's own files reach its call whole ---
def _workspace(root: Path, ids, long_id):
    for cid in ids:
        path = root / "app" / "actions" / f"{cid.replace('-', '_')}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "def run(payload):\n    return payload\n"
        if cid == long_id:
            body += "".join(f"# line {n:05d}\n" for n in range(grader._FILE_CHARS // 10)) + "TAIL_MARKER = 1\n"
        path.write_text(body)
    return root


@pytest.mark.parametrize("bp", [BPS[0], {"capabilities": [{"id": "bay-scan", "description": "Scan a bay."}, {"id": "dock_log", "description": "Log a dock."}]}])
def test_a_long_capability_file_is_whole_in_its_own_call_only(bp, tmp_path):
    ids = [c["id"] for c in bp["capabilities"]]
    long_id = ids[0]
    root = _workspace(tmp_path, ids, long_id)
    seen = {}

    def complete(messages):
        body = json.loads(messages[-1]["content"])
        for q in body["questions"]:
            seen[q["id"]] = body["rendered_product"]
        return "[]"

    l2.run_layer2(INPUTS, bp, complete, render=lambda stems: grader.render_product(root, first=stems))
    qs = l2.layer2_questions(bp)
    own, other = seen[qs[0]["id"]], seen[qs[1]["id"]]
    assert "TAIL_MARKER" in own and "truncated by the grader" not in own
    assert own.index(long_id.replace("-", "_")) < own.index(ids[1].replace("-", "_"))
    assert "TAIL_MARKER" not in other and "truncated by the grader" in other
    tail = [q for q in qs if not q["capability"]]
    assert seen[tail[-1]["id"]] == INPUTS["rendered_product"]


# --- one failed call opens only its own questions ---
@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("bad", ["not json", "{}"])
def test_one_failed_call_opens_only_its_own_questions(bp, bad):
    qs = l2.layer2_questions(bp)
    first = qs[0]["id"]
    good = _answer_all()

    def complete(messages):
        return bad if _sent(messages)[0]["id"] == first else good(messages)

    out = l2.run_layer2(INPUTS, bp, complete)
    assert out["ran"] is True
    assert len(out["open_fails"]) == 1 and qs[0]["text"] in out["open_fails"][0]
    assert [v["question"] for v in out["verdicts"]] == [q["text"] for q in qs[1:]]


@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("bad", ["not json", "{}"])
def test_ran_is_false_only_when_every_call_failed(bp, bad):
    out = l2.run_layer2(INPUTS, bp, lambda m: bad)
    qs = l2.layer2_questions(bp)
    assert out["ran"] is False and out["verdicts"] == []
    assert len(out["open_fails"]) == len(qs)
    assert all(q["text"] in f for q, f in zip(qs, out["open_fails"]))


# --- the writer's context is still refused ---
@pytest.mark.parametrize("bp", BPS)
@pytest.mark.parametrize("key", ["writer_prompt", "writer_transcript"])
def test_writer_context_still_raises_context_leak(bp, key):
    calls = []
    with pytest.raises(l2.ContextLeak, match=key):
        l2.run_layer2({**INPUTS, key: "x"}, bp, lambda m: calls.append(m) or "[]")
    assert calls == []
