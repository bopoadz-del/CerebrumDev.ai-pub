"""UI steps are derived from the brief.

A brief that asks for documents is graded on the document steps. A brief
that does not contain them does not run them: they are not applicable, not
passed, and not a failure. A brief that cannot be read keeps every step.

The briefs below are synthetic. Nothing here names a product, a client or a
probe.
"""

from __future__ import annotations

import json

import pytest

import app.factory.build.product_grader.ui_grader as ug
from app.factory.build.architect import NARRATIVE_SEPARATOR
from app.factory.build.grader import reason_classes
from app.factory.build.product_grader.grade_md import render_md

DOCUMENT_KINDS = frozenset({"attach_pdf", "attach_xlsx", "parsed", "cited_answer"})

BP = {
    "product_id": "qty-desk",
    "product_name": "Qty Desk",
    "summary": "quantities on hand",
    "ui_modules": ["count_board"],
    "capabilities": [
        {"id": "qty_on_hand", "description": "Record the quantity on hand."},
        {"id": "reorder_point", "description": "Record the reorder point."},
    ],
}

#: Two ways a brief asks for documents, and two that do not.
DOCUMENT_BRIEFS = [
    "Operators upload a PDF and a spreadsheet and must get a cited answer.",
    "Each record cites the source spreadsheet it was taken from.",
]
PLAIN_BRIEFS = [
    "Operators record the quantity on hand for each item and the reorder point.",
    "The count is compared with the reorder point and a shortfall is flagged.",
]

#: The Factory contract always names a document field. That sentence is the
#: contract, not the customer's ask.
_CONTRACT = (
    "CUT 1 — STEP 0 INVENTORY + STOP\n"
    "When a retrieval surface is owed, ingest takes the document text.\n"
)


def _kinds(brief):
    return {step.kind for step in ug.ui_plan(BP, brief)}


def _report(names, failed=()):
    return {
        "suites": [{
            "specs": [{
                "title": name,
                "tests": [{"results": [{"status": "failed" if name in failed else "passed"}]}],
            } for name in names],
        }],
    }


def _plan_names(brief):
    return [step.name for step in ug.ui_plan(BP, brief)]


# --- the two briefs ----------------------------------------------------------


@pytest.mark.parametrize("brief", DOCUMENT_BRIEFS)
def test_a_document_brief_keeps_and_grades_document_steps(brief):
    assert DOCUMENT_KINDS <= _kinds(brief)
    assert _plan_names(brief) == _plan_names(ug._UNSET)
    assert ug._question(BP, brief) == ug._question(BP)
    assert "attached document" in ug.render_spec(BP, brief)
    names = _plan_names(brief)
    failed = "attach:pdf"
    grade = ug.parse_report(_report(names, failed={failed}), BP, brief)
    assert not grade.ok
    assert failed in grade.failing
    assert grade.not_applicable == []
    assert grade.brief_note == ""
    passed = ug.parse_report(_report(names), BP, brief)
    assert passed.ok
    assert all(step["passed"] is True for step in passed.steps)
    assert {step["kind"] for step in passed.steps} >= DOCUMENT_KINDS


@pytest.mark.parametrize("brief", PLAIN_BRIEFS)
def test_a_brief_with_no_documents_does_not_run_document_steps(brief, tmp_path):
    kinds = _kinds(brief)
    assert not (kinds & DOCUMENT_KINDS)
    assert {"load", "bind", "honest_error", "screen", "route"} <= kinds
    spec = ug.render_spec(BP, brief)
    assert "attached document" not in spec
    plan = json.loads(spec.split("const PLAN = ", 1)[1].split(";\n", 1)[0])
    assert not ({step["kind"] for step in plan} & DOCUMENT_KINDS)
    assert ug._question(BP, brief) == ""
    assert ug.write_fixtures(BP, tmp_path / "fx", brief) == {}
    names = _plan_names(brief)
    grade = ug.parse_report(_report(names), BP, brief)
    assert grade.ok, grade.failing
    assert {step["kind"] for step in grade.not_applicable} == DOCUMENT_KINDS
    assert all(step.get("passed") is not True for step in grade.not_applicable)
    assert all(step["status"] == ug.NOT_APPLICABLE for step in grade.not_applicable)
    assert all(step["applicable"] is False for step in grade.not_applicable)
    failed = ug.parse_report(_report(names, failed={"load:/"}), BP, brief)
    assert not failed.ok
    assert failed.failing == ["load:/"]
    assert not any(step["kind"] in DOCUMENT_KINDS for step in failed.steps)


def test_document_steps_are_one_subject():
    """Any document ask keeps every document step; the kinds are not special cases."""
    assert DOCUMENT_KINDS == frozenset(
        kind for kind, subject in ug.KIND_SUBJECT.items() if subject == "documents"
    )
    only_pdf = "Operators upload a PDF of the count."
    assert DOCUMENT_KINDS <= _kinds(only_pdf)


def test_a_whole_token_decides_not_a_substring():
    brief = "The screen shows documentation for the count."
    assert not (_kinds(brief) & DOCUMENT_KINDS)


# --- unreadable brief: strict, and said --------------------------------------


@pytest.mark.parametrize("brief", [None, "", "   ", 0, {"summary": ""}, {"notes": 4}])
def test_an_unreadable_brief_keeps_every_step_and_says_so(brief):
    assert _plan_names(brief) == _plan_names(ug._UNSET)
    assert DOCUMENT_KINDS <= _kinds(brief)
    names = _plan_names(brief)
    grade = ug.parse_report(_report(names), BP, brief)
    assert grade.ok
    assert grade.not_applicable == []
    assert grade.brief_note == ug.BRIEF_UNREADABLE_NOTE
    missing = ug.parse_report(_report(names[1:]), BP, brief)
    assert not missing.ok
    assert names[0] in missing.failing


# --- the factory contract is not the brief -----------------------------------


def test_the_factory_contract_does_not_invent_a_document_ask():
    plain = PLAIN_BRIEFS[0]
    wrapped = (
        f"USER BRIEF\n{plain}\n\n"
        f"COMPILED C-BRIEF\n{_CONTRACT}\n\n"
        "CAPABILITIES\n- qty_on_hand: Record the quantity on hand.\n"
    )
    text = ug.brief_for_steps(BP, supplied=plain, compiled=wrapped)
    assert text is not None
    assert "document text" not in text
    assert not (_kinds(text) & DOCUMENT_KINDS)

    narrated = plain + NARRATIVE_SEPARATOR + _CONTRACT
    narrated_text = ug.brief_for_steps({"capabilities": []}, compiled=narrated)
    assert narrated_text == plain
    assert not (_kinds(narrated_text) & DOCUMENT_KINDS)

    contract_only = ug.brief_for_steps({"capabilities": []}, compiled=_CONTRACT)
    assert contract_only is None
    assert DOCUMENT_KINDS <= _kinds(contract_only)


def test_a_document_ask_survives_beside_the_factory_contract():
    ask = DOCUMENT_BRIEFS[0]
    wrapped = (
        f"USER BRIEF\n{ask}\n\n"
        f"COMPILED C-BRIEF\n{_CONTRACT}\n\n"
        "CAPABILITIES\n- qty_on_hand: Record the quantity on hand.\n"
    )
    text = ug.brief_for_steps(BP, compiled=wrapped)
    assert text is not None and "PDF" in text
    assert DOCUMENT_KINDS <= _kinds(text)


def test_the_blueprint_ask_is_the_brief_when_no_other_text_is_supplied():
    document_bp = dict(BP, summary="Operators upload a PDF and need a cited answer.")
    plain_bp = dict(BP)
    assert DOCUMENT_KINDS <= _kinds(ug.brief_for_steps(document_bp))
    assert not (_kinds(ug.brief_for_steps(plain_bp, compiled=_CONTRACT)) & DOCUMENT_KINDS)


# --- the run itself ----------------------------------------------------------


def _grade(tmp_path, brief, failed=()):
    names = _plan_names(brief)

    def fake(cmd, **kw):
        open(kw["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"], "w").write(
            json.dumps(_report(names, failed=failed))
        )

    return ug.grade_ui(BP, tmp_path, base_url="http://x", run=fake, brief=brief), names


@pytest.mark.parametrize("brief", DOCUMENT_BRIEFS)
def test_grade_ui_runs_document_steps_for_a_document_brief(tmp_path, brief):
    grade, names = _grade(tmp_path, brief, failed={"attach:pdf"})
    spec = (tmp_path / "grade_ui.spec.ts").read_text(encoding="utf-8")
    assert "attach:pdf" in spec and "attached document" in spec
    assert (tmp_path / "fixtures" / "fixture.pdf").is_file()
    assert (tmp_path / "fixtures" / "fixture.xlsx").is_file()
    assert not grade.ok and "attach:pdf" in grade.failing
    assert set(names) >= {step["name"] for step in grade.steps}


@pytest.mark.parametrize("brief", PLAIN_BRIEFS)
def test_grade_ui_does_not_run_or_fail_on_steps_the_brief_omits(tmp_path, brief):
    grade, _names = _grade(tmp_path, brief)
    spec = (tmp_path / "grade_ui.spec.ts").read_text(encoding="utf-8")
    plan = json.loads(spec.split("const PLAN = ", 1)[1].split(";\n", 1)[0])
    assert not ({step["kind"] for step in plan} & DOCUMENT_KINDS)
    assert "attached document" not in spec
    assert not (tmp_path / "fixtures" / "fixture.pdf").exists()
    assert grade.ok
    ui_reasons = [row for row in reason_classes({"layer1": {"ui": grade.to_dict(), "functions": {}}, "layer2": {"ran": True}}, []) if row["layer"] == "ui"]
    assert ui_reasons == []
    rendered = render_md({
        "product_id": "qty-desk",
        "certified": True,
        "layer1": {"ok": True, "functions": {}, "ui": grade.to_dict()},
        "layer2": {"ran": False, "verdicts": [], "open_fails": []},
        "next_tasks": [],
    })
    assert "UI steps not applicable:" in rendered
    assert "attach:pdf" in rendered
    assert "not applicable" in rendered
    na_line = next(line for line in rendered.splitlines() if line.startswith("- UI steps not applicable:"))
    assert "passed" not in na_line
