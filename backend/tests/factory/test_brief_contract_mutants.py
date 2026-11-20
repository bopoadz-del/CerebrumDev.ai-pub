"""The contract rules CI-M found unguarded in customer_brief and intake.

Each test fails on the exact mutant the mutation gate plants (a constant
return, or a dropped first rule) and passes on the real code. The rule names
appear in this file's text on purpose: the gate runs the test files that name
a function before the long brief suites, so the kill lands immediately. Two
of these mutants previously ran the whole mapped suite past its 600s budget
without being detected -- the tests here are what notice them.

Covers: _from_mapping (a mapping with no ask), _canonical (a mapping brief
quoted as canonical text), stage_quotes (an unreadable brief has no quotes),
file_types_of (a name with no suffix contributes no type), and
chat_turns_from_session (the stored brief becomes one numbered turn).
"""

from __future__ import annotations

import pytest

from app.factory.build.customer_brief import (
    STAGES,
    emit_brief,
    file_types_of,
    read_brief,
    stage_quotes,
)
from app.factory.build.intake_blueprint import chat_turns_from_session

MAPPING_A = {
    "ask": "Count the items on hand.",
    "documents": {"uploads": False, "file_types": []},
}
MAPPING_B = {
    "ask": "Upload the sheet.",
    "documents": {"uploads": True, "file_types": ["XLSX"]},
}

QUOTE_A = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'
QUOTE_B = '{"ask": "Upload the sheet.", "documents": {"file_types": ["xlsx"], "uploads": true}}'


@pytest.mark.parametrize(
    "raw",
    [
        {"documents": {"uploads": False, "file_types": []}},
        {"ask": "   ", "documents": {"uploads": False, "file_types": []}},
    ],
)
def test_a_mapping_without_an_ask_is_incomplete(raw):
    """A dropped ``if "ask" not in value`` rule reads a mapping with no ask
    as complete (and then crashes on ``None.strip()``); both cases must be
    reported, never read."""
    record, problems = read_brief(raw)
    assert record is None
    assert problems == ["ask"]


@pytest.mark.parametrize("raw,want", [(MAPPING_A, QUOTE_A), (MAPPING_B, QUOTE_B)])
def test_a_mapping_brief_is_quoted_as_canonical_text(raw, want):
    """A constant ``_canonical`` return leaves the record with no text to
    quote -- the writer, grader and tester would each quote nothing."""
    record, problems = read_brief(raw)
    assert problems == []
    assert record is not None
    assert record.text == want
    assert stage_quotes(raw) == {stage: want for stage in STAGES}


@pytest.mark.parametrize("raw", ["not a brief", '{"ask": "only ask"}'])
def test_an_incomplete_brief_has_no_stage_quotes(raw):
    """A dropped ``if text is None: return {}`` rule hands every stage a
    ``None`` quote -- the stages must quote nothing, not quote None."""
    assert stage_quotes(raw) == {}


@pytest.mark.parametrize(
    "names,want",
    [
        (["notes", "Report.PDF", "sheet.xlsx"], ["pdf", "xlsx"]),
        (["notes", "trailing."], []),
    ],
)
def test_upload_types_come_only_from_a_files_own_suffix(names, want):
    """A dropped suffix guard takes the whole filename as a type ("notes"),
    which then decides which document steps run."""
    assert file_types_of(names) == want


class _ProductDesign:
    def __init__(self, brief):
        self.brief = brief


class _SessionState:
    def __init__(self, brief):
        self.product_design = _ProductDesign(brief)
        self.chat_history = []


@pytest.mark.parametrize("ask", ["Count the items on hand.", "Upload the pdf."])
def test_the_session_brief_becomes_one_numbered_turn(ask):
    """A constant return hands the collector no turns; a dropped
    ``if pd is not None`` rule leaves the brief empty -- either way the
    customer's own words never reach the intake."""
    turns = chat_turns_from_session(_SessionState(emit_brief(ask, [])))
    assert turns == [{"turn": 1, "role": "user", "text": ask}]
