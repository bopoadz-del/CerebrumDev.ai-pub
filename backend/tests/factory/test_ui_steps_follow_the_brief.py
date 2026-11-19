"""The customer brief is one format, read by the writer, the grader and the tester.

Synthetic records only. A document brief keeps the document UI steps. A brief
with no documents does not run them. An unreadable brief, or one missing a
required field, starts nothing — including the writer.
"""

from __future__ import annotations

import json

import pytest

import app.factory.build.product_grader.ui_grader as ug
from app.factory.build.authority import BuildRole
from app.factory.build.customer_brief import REQUIRED_FIELDS, format_report, read_brief, stage_quotes
from app.factory.build.ledger import EventKind
from app.factory.planner import ProductPlan
from app.factory.build.product_grader.layer2 import layer2_messages
from app.factory.build.roles_handlers import _compiled_writer_brief
from app.factory.build.runner import Outcome, RoleRunner
from app.factory.build.writer_prompt import render_writer_prompt

DOCUMENT_KINDS = frozenset({"attach_pdf", "attach_xlsx", "parsed", "cited_answer"})

BP = {
    "product_id": "sample",
    "product_name": "Sample",
    "summary": "items on hand",
    "ui_modules": ["count_board"],
    "capabilities": [
        {"id": "qty_on_hand", "description": "Record the quantity on hand."},
        {"id": "reorder_point", "description": "Record the reorder point."},
    ],
}


def _brief(ask, uploads, file_types):
    return json.dumps(
        {"ask": ask, "documents": {"uploads": uploads, "file_types": list(file_types)}},
        sort_keys=True,
    )


DOC = _brief("Count the items and upload the files.", True, ["pdf", "xlsx"])
PLAIN = _brief("Count the items on hand.", False, [])


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


class _Workspace:
    def exists(self, _rel):
        return False


class _Ctx:
    def __init__(self, brief):
        self.state = {"brief": brief}
        self.blueprint = type("B", (), {"capabilities": [], "summary": ""})()
        self.workspace = _Workspace()


def _runner(tmp_path, brief):
    plan = ProductPlan(product_id="sample", capabilities=[], fail_closed=True)
    return RoleRunner(
        BP,
        tmp_path / "build",
        plan=plan,
        blocks_root=tmp_path / "blocks",
        inputs_hash="inputs",
        brief=brief,
        budget=__import__(
            "app.factory.build.runner", fromlist=["BuildBudget"]
        ).BuildBudget(max_rework=0, wall_clock_s=30),
    )


# --- documents field ---------------------------------------------------------


def test_a_document_brief_keeps_and_grades_every_document_step(tmp_path):
    assert DOCUMENT_KINDS <= _kinds(DOC)
    assert [s.name for s in ug.ui_plan(BP, DOC)] == [s.name for s in ug.ui_plan(BP)]
    assert ug._question(BP, DOC) == ug._question(BP)
    assert "attached document" in ug.render_spec(BP, DOC)
    names = [s.name for s in ug.ui_plan(BP, DOC)]
    failed = ug.parse_report(_report(names, failed={"attach:pdf"}), BP, DOC)
    assert not failed.ok and "attach:pdf" in failed.failing
    fx = ug.write_fixtures(BP, tmp_path / "fx", DOC)
    assert fx["pdf"].is_file() and fx["xlsx"].is_file()


def test_a_brief_with_no_documents_does_not_run_those_steps(tmp_path):
    assert not (_kinds(PLAIN) & DOCUMENT_KINDS)
    assert {"load", "bind", "honest_error", "screen", "route"} <= _kinds(PLAIN)
    assert ug._question(BP, PLAIN) == ""
    assert "attached document" not in ug.render_spec(BP, PLAIN)
    assert ug.write_fixtures(BP, tmp_path / "fx", PLAIN) == {}
    names = [s.name for s in ug.ui_plan(BP, PLAIN)]
    grade = ug.parse_report(_report(names), BP, PLAIN)
    assert grade.ok
    assert {s["kind"] for s in grade.not_applicable} == DOCUMENT_KINDS
    assert all(s.get("passed") is not True and s["status"] == ug.NOT_APPLICABLE for s in grade.not_applicable)
    missed = ug.parse_report(_report(names, failed={"load:/"}), BP, PLAIN)
    assert not missed.ok and missed.failing == ["load:/"]


def test_prose_that_mentions_a_file_does_not_select_document_steps():
    assert ug.ui_plan(BP, "upload a PDF and cite the spreadsheet") == []
    grade = ug.parse_report({}, BP, "upload a PDF")
    assert grade.failing == [ug.BRIEF_UNREADABLE]
    assert not grade.steps


# --- one quote for every stage ----------------------------------------------


@pytest.mark.parametrize("raw", [DOC, PLAIN])
def test_writer_grader_and_tester_quote_the_brief_verbatim(raw):
    quotes = stage_quotes(raw)
    assert quotes["writer"] == quotes["grader"] == quotes["tester"] == raw
    written = _compiled_writer_brief(_Ctx(raw))
    assert written.startswith(raw)
    rendered = render_writer_prompt(type("B", (), {
        "product_id": "sample", "product_name": "Sample", "vertical": "v", "summary": "s",
    })(), brief=written)
    assert raw in rendered
    message = layer2_messages(
        {"compiled_brief": quotes["grader"], "rendered_product": "tree", "layer1": {"ok": True}},
        [{"id": "q1", "text": "shown?"}],
    )
    body = json.loads(message[-1]["content"])
    assert body["compiled_brief"] == raw


# --- the gate, before anything starts ----------------------------------------


@pytest.mark.parametrize("raw,missing", [
    ("this is not the brief format", ["unreadable"]),
    (json.dumps({"ask": "Count the items."}), ["documents"]),
])
def test_an_unreadable_or_incomplete_brief_starts_nothing(tmp_path, raw, missing):
    record, problems = read_brief(raw)
    assert record is None
    assert problems == missing
    runner = _runner(tmp_path, raw)
    outcome = runner.run()
    assert outcome.outcome is Outcome.FAILED_GATE
    assert outcome.detail == format_report(missing)
    assert "brief incomplete/unreadable" in outcome.detail
    for name in missing:
        assert name in outcome.detail
    started = [e.role for e in runner.ledger.events() if e.kind is EventKind.PHASE_STARTED]
    assert BuildRole.WRITER not in started
    assert BuildRole.TESTER not in started
    assert started == []


def test_a_complete_brief_names_its_required_fields():
    record, problems = read_brief(DOC)
    assert problems == []
    assert record is not None and record.uploads is True
    assert set(REQUIRED_FIELDS) <= {"ask", "documents"}
    plain, _ = read_brief(PLAIN)
    assert plain is not None and plain.uploads is False
