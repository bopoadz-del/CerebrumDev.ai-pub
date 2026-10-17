"""What the Floor model is told about the reasoning kit's questions.

This is the only place the domain owner's question sheet reaches the model that
runs a build. If it tells the model the wrong thing, the model asks the wrong
questions or, worse, presents a derived question as the domain's own and gets an
answer no rule can then use.

The assertions are about what the prompt CANNOT say:

  * never that an un-interviewed domain is in hand
  * never a derived question without saying it is derived
  * never a paraphrase of the owner's wording
  * never that an answer is held because a question was asked
"""
from __future__ import annotations

import pathlib
import types

import pytest
import yaml

from app.factory import platform_chat_llm


def _state(vertical):
    return types.SimpleNamespace(
        product_design=types.SimpleNamespace(
            blueprint=types.SimpleNamespace(vertical=vertical)))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A Store the facts builder will read, wherever a test puts a kit in it."""
    root = tmp_path / "store"

    def resolve():
        return root if (root / "app" / "blocks").is_dir() else None

    module = pytest.importorskip("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root", resolve)

    def put(kit: str, manifest: dict, questions: dict = None,
            invariants: bool = True) -> pathlib.Path:
        where = root / "app" / "blocks" / kit
        where.mkdir(parents=True, exist_ok=True)
        (where / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
        # A kit is its two declarative files (half a kit is not a kit).
        if invariants and not (where / "invariants.yaml").exists():
            (where / "invariants.yaml").write_text("invariants: []\n", encoding="utf-8")
        if questions is not None:
            (where / "questions.yaml").write_text(
                yaml.safe_dump(questions, allow_unicode=True), encoding="utf-8")
        return where

    return put


OWNER_SHEET = {
    "kit": "fitout",
    "title": "Fit-Out — Questions I Cannot Answer",
    "source_document": "docs/kit_questions/fitout.md",
    "answer_format": ["value", "unit", "quality band", "market", "source", "date",
                      "confirmed or indicative"],
    "sections": {"B": {"title": "Rates"}, "M": {"title": "Incidents"}},
    "questions": [
        {"id": "B.1", "section": "B", "gate": True,
         "text": "Cost per m² all-in by your quality band — basic, standard, premium, "
                 "super-prime — and what you define each band as."},
        {"id": "M.11", "section": "M", "gate": False,
         "text": "What a PM new to fit-out most commonly gets wrong in their first year."},
    ],
}

DERIVED_MANIFEST = {
    "kit": "fitout", "version": 1,
    "quantities": {"rate": {"units": ["currency_per_m2"]}},
    "figures": {"rate": {"value": None, "question": "What is the rate for this platform?"}},
}


def test_the_owners_sheet_is_what_the_model_is_given(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))

    assert "DOMAIN OWNER'S OWN question sheet" in facts
    assert "docs/kit_questions/fitout.md" in facts
    # The owner's wording, verbatim, and the id so an answer can find its question.
    assert "[B.1]" in facts
    assert "basic, standard, premium, super-prime" in facts
    # And never the derived one when a sheet exists.
    assert "What is the rate for this platform?" not in facts


def test_the_model_is_told_the_fields_an_answer_must_carry(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    for field in ("quality_band", "market", "confirmed_or_indicative"):
        assert field in facts
    assert "cannot be cited" in facts


def test_only_gating_questions_are_offered_to_ask(store):
    """The GAP questions are worth having and are not what a build stops for. A
    model that asks "what does a new PM get wrong" during a build is spending a
    bounded question round on something that blocks nothing."""
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "2 questions, 1 of them gating" in facts
    assert "[M.11]" not in facts


def test_an_unmarked_question_is_counted_as_gating(store):
    sheet = dict(OWNER_SHEET, questions=[
        {"id": "1.1", "section": "B", "gate": None, "text": "An unmarked question."}])
    store("fitout", DERIVED_MANIFEST, sheet)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "1 questions, 1 of them gating" in facts


def test_the_model_is_told_never_to_invent_a_value_or_claim_one_is_held(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "NEVER invent a value" in facts
    assert "never tell the user a figure is in hand because the question was asked" in facts
    assert "not a stub" in facts


def test_a_derived_question_is_labelled_derived(store):
    """A model that presents "what is the rate?" as the domain's own question
    invites an answer that no rule can then use."""
    store("fitout", DERIVED_MANIFEST)  # no sheet
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "NO owner question sheet" in facts
    assert "DERIVED" in facts
    assert "not the domain owner's own wording" in facts
    assert "What is the rate for this platform?" in facts


def test_a_vertical_with_no_kit_does_not_let_the_model_claim_gating(store):
    store("fitout", DERIVED_MANIFEST, OWNER_SHEET)
    facts = platform_chat_llm._reasoning_kit_facts(_state("no_such_vertical"))
    assert "none matched" in facts
    assert "do not claim" in facts


def test_a_kit_declaring_no_figures_is_never_reported_as_all_answered(store):
    """The first version of this said "every figure already answered" for a kit
    that declared none at all -- the most misleading thing it could have said."""
    store("fitout", {"kit": "fitout", "version": 1,
                     "quantities": {"rate": {"units": ["currency_per_m2"]}}})
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "NO question list" in facts
    assert "refuse every figure" in facts
    assert "already answered" not in facts


def test_an_unreachable_store_says_so_rather_than_promising_a_gate(store, monkeypatch):
    module = pytest.importorskip("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root", lambda: None)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "Store unreachable" in facts


def test_the_facts_never_raise_into_the_chat_turn(store, monkeypatch):
    """Kit facts are context. A broken Store must not take the Floor down with it."""
    module = pytest.importorskip("app.factory.blocks_source")

    def boom():
        raise RuntimeError("the Store exploded")

    monkeypatch.setattr(module, "resolve_blocks_root", boom)
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "unavailable" in facts


# ── the kit's own figure register ──────────────────────────────────────────

FILLED_REGISTER = {
    "facility": "facility_01",
    "source": "domain encoding sheet, facility_01",
    "scope": "facility-specific — never carry to another site",
    "design_basis": {
        "pue_design": {"value": 1.4, "unit": None},
        "generator_fuel_autonomy_hours": {"value": 120, "unit": "hours"},
        "pue_guaranteed": {"value": None, "unit": None},
    },
}


def _put_register(store, kit, manifest, register, questions=None):
    where = store(kit, manifest, questions)
    import yaml as _y
    (where / "design_basis.yaml").write_text(
        _y.safe_dump(register, allow_unicode=True), encoding="utf-8")


def test_the_model_is_told_which_figures_the_platform_already_holds(store):
    """datacentre's register is filled in. Asking the user for figures on file
    wastes a bounded question round and invites them to restate what is recorded."""
    _put_register(store, "datacentre", DERIVED_MANIFEST, FILLED_REGISTER)
    facts = platform_chat_llm._reasoning_kit_facts(_state("datacentre"))
    assert "ALREADY HOLDS 2 of 3 figures" in facts
    assert "facility_01" in facts
    assert "do not ask for those again" in facts
    assert "pue_guaranteed" in facts, "the one open figure must still be named"


def test_an_empty_register_is_reported_as_nothing_answered_not_nothing_to_ask(store):
    empty = dict(FILLED_REGISTER, design_basis={
        "twist_limit_mm": {"value": None, "unit": "mm"},
        "sft_degrees_c": {"value": None, "unit": "degC"},
    })
    _put_register(store, "rail", DERIVED_MANIFEST, empty)
    facts = platform_chat_llm._reasoning_kit_facts(_state("rail"))
    assert "EVERY ONE IS EMPTY" in facts
    assert "no interview has run" in facts
    assert "ALREADY HOLDS" not in facts


def test_the_register_is_reported_alongside_the_owner_sheet_not_instead_of_it(store):
    _put_register(store, "rail", DERIVED_MANIFEST, FILLED_REGISTER,
                  questions=dict(OWNER_SHEET, kit="rail"))
    facts = platform_chat_llm._reasoning_kit_facts(_state("rail"))
    assert "DOMAIN OWNER'S OWN question sheet" in facts
    assert "ALREADY HOLDS" in facts


def test_a_filled_register_is_never_reported_as_a_kit_that_can_answer_nothing(store):
    """The manifest declares no figures AND the register is full. The old "declares
    NO question list, will refuse every figure" line would be flatly false."""
    _put_register(store, "datacentre", {
        "kit": "datacentre", "version": 1,
        "quantities": {"pue": {}},
    }, FILLED_REGISTER)
    facts = platform_chat_llm._reasoning_kit_facts(_state("datacentre"))
    assert "ALREADY HOLDS" in facts
    assert "refuse every figure it needs" not in facts


# ── the Floor's prompt must carry what it was given ────────────────────────

def test_a_long_owner_question_is_carried_whole_not_clipped(store):
    """Each question used to be capped at 220 characters, and the questions worth
    asking are the long ones. Fit-out's B.2 runs past 400: "your rate per package —
    partitions (plasterboard, glazed, demountable), ceilings (grid,
    plasterboard/feature), raised floor, ...". Clipped, the model was instructed to
    ask VERBATIM a question that had been cut mid-list."""
    long_question = (
        "Your rate per package — partitions (plasterboard, glazed, demountable), "
        "ceilings (grid, plasterboard/feature), raised floor, flooring (carpet, "
        "stone/tile, timber), joinery (standard, bespoke), wall finishes (paint, "
        "specialist), doors and ironmongery, MEP mechanical, MEP electrical, "
        "lighting, sprinkler modification, fire alarm modification, BMS integration, "
        "AV/IT, kitchen equipment, sanitaryware, fire stopping, T&C, prelims."
    )
    assert len(long_question) > 400
    sheet = dict(OWNER_SHEET, questions=[
        {"id": "B.2", "section": "B", "gate": True, "text": long_question}])
    store("fitout", DERIVED_MANIFEST, sheet)

    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert long_question in facts, "the owner's question arrived truncated"
    assert "prelims." in facts, "the tail of the list is where half the packages are"


def test_the_question_budget_drops_whole_questions_and_says_how_many(store):
    """When there are more questions than budget, whole ones are dropped and the
    count is stated — never a question cut in half."""
    many = [
        {"id": f"Q.{n}", "section": "B", "gate": True, "text": "q " * 2000}
        for n in range(20)
    ]
    store("fitout", DERIVED_MANIFEST, dict(OWNER_SHEET, questions=many))
    facts = platform_chat_llm._reasoning_kit_facts(_state("fitout"))
    assert "more gating" in facts
    assert "ask for them by id" in facts


# ── the conversation the model is shown ────────────────────────────────────

def _history(*turns):
    import types as _types

    return _types.SimpleNamespace(chat_history=[
        {"role": role, "content": content} for role, content in turns])


def test_a_long_pasted_brief_survives_the_next_turn_verbatim():
    """The current message goes in full, so a long brief was understood ONCE and
    then shredded to its first 600 characters on every turn after. The model asked
    again for what it had been told, or drafted on assumptions."""
    brief = ("Our fit-out business: we price per package, our market is Dubai, and "
             "the quality bands are basic, standard, premium, super-prime. " * 120).strip()
    assert len(brief) > 10_000

    shown = platform_chat_llm._conversation(
        _history(("user", brief), ("assistant", "Noted.")), "and the rates?")

    assert brief in shown, "the brief was truncated in the history the model sees"
    assert "elided" not in shown and "dropped" not in shown


def test_the_conversation_stays_inside_its_budget_and_says_what_it_dropped():
    turns = [("user", "x" * 30_000) for _ in range(10)]
    shown = platform_chat_llm._conversation(_history(*turns), "next")

    assert len(shown) <= platform_chat_llm._CONVERSATION_BUDGET_CHARS + 500
    assert "dropped to fit the context budget" in shown
    assert "ask rather than assume" in shown, (
        "a trimmed conversation must not read as the whole of what it was told"
    )


def test_one_turn_bigger_than_the_budget_keeps_its_start_and_its_end():
    """The end of a long brief is where the asks are. Keeping only the head loses
    the part that was the point of sending it."""
    huge = "START-OF-BRIEF" + ("y" * 200_000) + "AND-IT-MUST-HANDLE-VAT"
    shown = platform_chat_llm._conversation(_history(("user", huge)), "next")

    assert "START-OF-BRIEF" in shown
    assert shown.rstrip().endswith("AND-IT-MUST-HANDLE-VAT")
    assert "elided from the middle" in shown
    assert len(shown) <= platform_chat_llm._CONVERSATION_BUDGET_CHARS + 500


def test_the_budget_is_big_enough_to_be_worth_calling_a_budget():
    """A regression guard on the numbers themselves: 600 characters per turn was the
    defect, and a future 'tidy-up' that restores a small per-turn cap would bring it
    back silently."""
    assert platform_chat_llm._CONVERSATION_BUDGET_CHARS >= 100_000
    assert platform_chat_llm._HISTORY_TURNS >= 40
    assert not hasattr(platform_chat_llm, "_HISTORY_TURN_CHARS"), (
        "the per-turn cap is the wrong shape: it mutilates the longest turn, which "
        "is the one most worth keeping"
    )


def test_a_kit_only_the_store_knows_is_not_reported_as_no_kit_for_the_vertical(
        store, tmp_path):
    """"None matched for this vertical yet" is a claim about the DOMAIN, and it
    was being made from a stale copy of the Store's kit list.

    ``stadium_venue`` shipped in the Store while the Factory's alias table had no
    entry, so this leg told the model the vertical had no reasoning kit — and the
    model then correctly declined to promise gating for a domain that had 43
    invariants sitting in the Store.
    """
    where = store("a_kit_nobody_hardcoded", {
        "kit": "a_kit_nobody_hardcoded", "version": 1,
        "quantities": {"rate": {"units": ["currency_per_m2"]}},
        "figures": {"rate": {"value": None, "question": "What is the rate?"}},
    })
    (where / "invariants.yaml").write_text(yaml.safe_dump({"invariants": [
        {"id": "INV-1", "kind": "grounding", "severity": "refuse", "hook": "H3",
         "applies_to": {"quantity": "any"}, "message": "{quantity} is not grounded",
         "measurement": "20 probes. Before: n uncited. After: 0."}]}),
        encoding="utf-8")

    facts = platform_chat_llm._reasoning_kit_facts(_state("a_kit_nobody_hardcoded"))
    assert "none matched for this vertical" not in facts
    assert "a_kit_nobody_hardcoded" in facts


def test_half_a_kit_in_the_store_is_still_no_kit(store):
    """The leg asks the Store, which is not the same as trusting a directory: a
    manifest with no invariants would gate nothing, so it must not be announced
    as a kit that will gate this platform's figures."""
    store("half_a_kit", {
        "kit": "half_a_kit", "version": 1,
        "quantities": {"rate": {"units": ["currency_per_m2"]}},
    }, invariants=False)
    facts = platform_chat_llm._reasoning_kit_facts(_state("half_a_kit"))
    assert "none matched for this vertical" in facts
