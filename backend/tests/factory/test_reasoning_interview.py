"""The domain owner's own question sheet, as a built platform runs it.

The kits used to ask a question DERIVED from each quantity name — "what is the
rate?" — which is one number for a whole domain and unanswerable in practice. The
sheets in the Store's ``docs/kit_questions/`` are the owner's own words, carry the
owner's [GATE] / [GAP] mark, and name the fields an answer must arrive with.

What is asserted here is what decides whether the sheet is worth having:

  * the owner's wording WINS over the derived question where the sheet names a
    figure, and the derived one survives as the fallback where it does not
  * [GAP] never blocks; UNMARKED does, because a class that cannot be read must
    block rather than pass
  * an answer short of a field this domain's format requires is refused, not
    stored partially
  * a kit with no sheet is never reported as interviewed
  * an answer is durable or it is not reported as recorded
"""
from __future__ import annotations

import importlib
import sys

import pytest
import yaml

# `platform` is a conftest fixture: pytest resolves it by parameter name with no
# import, which is what keeps ruff's F811 away and keeps one definition of it.
from tests.factory.conftest import _figure

KIT_SHEET = {
    "kit": "probe",
    "title": "Probe — Questions I Cannot Answer",
    "source_document": "docs/kit_questions/probe.md",
    "answer_format": ["value", "unit", "code edition", "source", "date"],
    "sections": {"1": {"title": "Declared distances"}, "2": {"title": "Incidents"}},
    "questions": [
        {"id": "1.1", "section": "1", "gate": True, "covers": ["declared_distance"],
         "text": "Your declared distances per runway end — TORA, TODA, ASDA, LDA — "
                 "with the AIP table and its effective date."},
        {"id": "1.2", "section": "1", "gate": True,
         "text": "Who approves a temporary reduction, and your lead time."},
        {"id": "1.3", "section": "1", "gate": None,
         "text": "An unmarked question: the sheet marked neither GATE nor GAP."},
        {"id": "2.1", "section": "2", "gate": False,
         "text": "A figure taken from a superseded revision, and what it cost."},
    ],
}

ANSWER_FIELDS = {
    "unit": "m",
    "code_edition": "Annex 14, 8th edition",
    "source": "AIP declared distance table",
    "date": "2026-09-24",
}


@pytest.fixture
def sheeted(platform, tmp_path):
    """The built platform, with the owner's question sheet vendored beside the kit."""
    kit = tmp_path / "builtapp" / "reasoning" / "kit"
    (kit / "questions.yaml").write_text(
        yaml.safe_dump(KIT_SHEET, allow_unicode=True), encoding="utf-8")
    for name in [n for n in list(sys.modules)
                 if n.endswith("reasoning.kernel") or n.endswith("reasoning.pending")]:
        del sys.modules[name]
    return importlib.import_module("builtapp.reasoning.kernel")


# ── precedence: the owner's wording, with the derived one as fallback ──────

def test_a_covered_figures_question_is_the_owners_wording_not_the_derived_one(sheeted):
    pending = importlib.import_module("builtapp.reasoning.pending")
    question = pending.unanswered()["runway_13l_tora"]
    assert "1.1" in question and "TORA, TODA, ASDA, LDA" in question
    assert "Q5.3" not in question, (
        "the derived question won over the owner's own — precedence is the wrong way round"
    )


def test_a_figure_the_sheet_does_not_name_keeps_its_derived_question(sheeted):
    """Precedence, not replacement. Most quantities are named by no sheet question,
    and the derived one is all they have."""
    kernel = sheeted.ReasoningKernel()
    kernel.manifest["figures"]["unnamed_thing"] = {
        "value": None, "question": "Q9.9: derived, and the only question there is"}
    assert "Q9.9" in kernel.pending_questions()["unnamed_thing"]


# ── gating ────────────────────────────────────────────────────────────────

def test_an_unmarked_question_gates_and_a_gap_question_does_not(sheeted):
    state = sheeted.kernel.interview()
    assert {q["id"] for q in state["next"]} == {"1.1", "1.2", "1.3"}
    assert state["gating"] == 3 and state["questions"] == 4
    assert {q["marked"] for q in state["next"]} == {"GATE", "unmarked"}
    assert state["ready"] is False


def test_readiness_needs_every_gating_question_and_ignores_the_gap(sheeted):
    kernel = sheeted.kernel
    for qid in ("1.1", "1.2"):
        kernel.record_question_answer(qid, "answered", dict(ANSWER_FIELDS))
    assert kernel.interview()["ready"] is False, "the unmarked question still gates"
    kernel.record_question_answer("1.3", "answered", dict(ANSWER_FIELDS))
    state = kernel.interview()
    assert state["ready"] is True
    assert state["gaps_outstanding"] == 1, "the GAP is still worth asking, just not blocking"


def test_outstanding_questions_come_back_in_sheet_order(sheeted):
    ids = [q["id"] for q in sheeted.kernel.interview()["next"]]
    assert ids == ["1.1", "1.2", "1.3"]


def test_a_question_carries_its_section_title_so_the_ask_has_context(sheeted):
    first = sheeted.kernel.interview()["next"][0]
    assert first["section"] == "1" and first["section_title"] == "Declared distances"


# ── what a complete answer is, per domain ─────────────────────────────────

def test_required_fields_come_from_the_sheet_not_from_the_kernel(sheeted):
    kernel = sheeted.kernel
    assert kernel.required_answer_fields() == ["unit", "code_edition", "source", "date"]
    assert "value" not in kernel.required_answer_fields(), (
        "value is the answer itself, not a field that must accompany it"
    )


def test_an_answer_missing_a_field_this_domain_requires_is_refused(sheeted):
    kernel = sheeted.kernel
    with pytest.raises(ValueError) as exc:
        kernel.record_question_answer("1.1", "3200 m", {"unit": "m", "source": "AIP"})
    assert "code_edition" in str(exc.value) and "date" in str(exc.value)
    assert kernel.question_answers() == {}, "a refused answer must not be stored partially"


def test_a_blank_required_field_is_not_an_answer(sheeted):
    with pytest.raises(ValueError):
        sheeted.kernel.record_question_answer(
            "1.1", "3200 m", dict(ANSWER_FIELDS, code_edition=""))


def test_answering_a_question_the_sheet_never_asked_is_refused(sheeted):
    with pytest.raises(KeyError):
        sheeted.kernel.record_question_answer("99.99", "x", dict(ANSWER_FIELDS))


# ── answers never reach the kit ───────────────────────────────────────────

def test_a_question_answer_never_lands_in_the_kit(sheeted, tmp_path):
    kit = tmp_path / "builtapp" / "reasoning" / "kit"
    before = {p.name: p.read_bytes() for p in sorted(kit.iterdir())}
    sheeted.kernel.record_question_answer("1.1", "3200 m", dict(ANSWER_FIELDS))
    after = {p.name: p.read_bytes() for p in sorted(kit.iterdir())}
    assert before == after, (
        "the kit is a signed Store block shared by every customer; one client's "
        "declared distances reaching another's platform would arrive signed"
    )


def test_figure_answers_and_question_answers_do_not_overwrite_each_other(sheeted):
    """Two namespaces, because a sheet id and a quantity name are different kinds
    of key and a collision would silently answer the wrong thing."""
    kernel = sheeted.kernel
    kernel.record_answer("runway_13l_tora", 3200, answered_by="ops",
                         answered_at="2026-09-24", source="AIP")
    kernel.record_question_answer("1.1", "per end", dict(ANSWER_FIELDS))
    assert kernel.answers()["runway_13l_tora"]["value"] == 3200
    assert kernel.question_answers()["1.1"]["answer"] == "per end"


def test_an_answer_file_written_before_the_sheet_existed_is_still_read(
        sheeted, tmp_path, monkeypatch):
    """The store gained two namespaces. A platform that already holds figure
    answers in the old flat shape must keep them, not silently start over."""
    import json

    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "reasoning_answers.json").write_text(json.dumps({
        "runway_13l_tora": {"value": 3200, "answered_by": "ops",
                            "answered_at": "2026-09-24", "source": "AIP"}}),
        encoding="utf-8")
    kernel = sheeted.ReasoningKernel()
    assert kernel.answers()["runway_13l_tora"]["value"] == 3200
    value, refusal = kernel.figure_value("runway_13l_tora")
    assert value == 3200 and refusal is None


# ── a kit with no sheet ───────────────────────────────────────────────────

def test_a_kit_with_no_sheet_is_never_reported_as_interviewed(platform):
    """A kit with no sheet and a kit whose sheet is fully answered both have
    nothing outstanding. Reporting them alike would call a domain nobody has
    interviewed ready to gate."""
    state = platform.kernel.interview()
    assert state["questions_source"] == "derived"
    assert state["sheet_supplied"] is False
    assert state["ready"] is False
    assert "DERIVED" in state["note"]
    with pytest.raises(KeyError):
        platform.kernel.record_question_answer("1.1", "x", {})


def test_a_sheet_that_will_not_parse_disables_the_kit(platform, tmp_path):
    """Present but broken must disable. Falling back to "no sheet" would leave zero
    outstanding questions, which reads as a completed interview."""
    kit = tmp_path / "builtapp" / "reasoning" / "kit"
    (kit / "questions.yaml").write_text("questions: []\n", encoding="utf-8")
    kernel = platform.ReasoningKernel()
    assert kernel.enabled is False
    assert "sheet" in kernel.disabled_reason
    assert kernel.answer_time([_figure(platform)]).verdict == "refused"


def test_a_sheet_with_no_answer_format_disables_the_kit(platform, tmp_path):
    kit = tmp_path / "builtapp" / "reasoning" / "kit"
    (kit / "questions.yaml").write_text(yaml.safe_dump({
        "kit": "probe",
        "questions": [{"id": "1.1", "text": "?", "gate": True}],
    }), encoding="utf-8")
    kernel = platform.ReasoningKernel()
    assert kernel.enabled is False
    assert "answer_format" in kernel.disabled_reason


# ── answers must be durable, or not reported as recorded ──────────────────

def test_recording_with_no_durable_storage_raises_rather_than_using_the_cwd(
        sheeted, monkeypatch, tmp_path):
    """It used to fall back to ".", the process working directory. That reads as
    working — the answer saves and comes back — and then a restart under a
    different working directory, or a second worker started elsewhere, silently
    has none of them. This is also why the suite stopped writing
    reasoning_answers.json into the repository root and passing or failing on what
    a previous run left behind."""
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    kernel = sheeted.ReasoningKernel()

    assert kernel.answers() == {} and kernel.question_answers() == {}
    with pytest.raises(RuntimeError) as exc:
        kernel.record_answer("runway_13l_tora", 3200, answered_by="ops",
                             answered_at="2026-09-24", source="AIP")
    assert "STORAGE_PATH" in str(exc.value)
    assert not (tmp_path / "reasoning_answers.json").exists(), (
        "an answer reached the working directory after the write was refused"
    )
    # And the figure is still refused, which is the correct end state.
    value, refusal = kernel.figure_value("runway_13l_tora")
    assert value is None and refusal


def test_data_dir_also_counts_as_durable_storage(sheeted, monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    kernel = sheeted.ReasoningKernel()
    kernel.record_answer("runway_13l_tora", 3200, answered_by="ops",
                         answered_at="2026-09-24", source="AIP")
    assert (tmp_path / "data" / "reasoning_answers.json").is_file()


# ── the routes the operator actually calls ────────────────────────────────

def test_the_emitted_routes_declare_both_interview_paths_and_methods():
    """The routes module cannot be imported in a temp package (it needs the
    product's fastapi app, auth and tenancy), so its shape is checked here. A
    typo in a path would otherwise ship and only be found by an operator who
    could not answer a question."""
    import ast

    from app.factory.build.reasoning_socket import render_routes

    source = render_routes()
    tree = ast.parse(source)
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            attribute = decorator.func
            if isinstance(attribute, ast.Attribute) and decorator.args:
                first = decorator.args[0]
                if isinstance(first, ast.Constant):
                    routes.add((attribute.attr.upper(), first.value))
    assert ("GET", "/v1/reasoning/interview") in routes
    assert ("POST", "/v1/reasoning/interview") in routes
    assert ("GET", "/v1/reasoning/pending") in routes
    assert ("POST", "/v1/reasoning/answer") in routes


def test_the_answer_route_turns_a_failed_persist_into_a_500_not_an_ok():
    """ok over an operation that did nothing is the defect class this layer
    exists to catch, and it already shipped here once."""
    from app.factory.build.reasoning_socket import render_routes

    source = render_routes()
    # Both POST handlers must map RuntimeError (the read-back failure) to 500.
    assert source.count("except RuntimeError as exc:") == 2
    assert source.count("status_code=500") == 2
