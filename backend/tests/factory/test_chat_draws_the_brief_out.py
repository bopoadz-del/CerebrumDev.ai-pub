"""The chat keeps asking while the customer is still answering.

Two rounds was a cap on curiosity: the chat drafted while the customer was
still willing to talk, and the coding agent then invented what nobody had
said. FinOps (sess_065fc3eac75c4f62) was drafted without a country and
without a single approval limit, so the agent chose UK VAT at 20% for a Dubai
business and invented 1,000 / 10,000 approval tiers.

What the customer tells us before the build is free. What the agent assumes
costs a rework round, or a platform built for the wrong country. The customer
ends the questions by saying build now -- not a counter.
"""

from __future__ import annotations

from app.factory.platform_chat_llm import MAX_ELICITATION_ROUNDS, _SYSTEM


def _ask_section() -> str:
    return _SYSTEM[_SYSTEM.index("- ask_user:"): _SYSTEM.index("- draft_platform:")]


def test_there_is_room_to_draw_the_brief_out():
    assert MAX_ELICITATION_ROUNDS >= 6


def test_the_chat_is_told_to_keep_asking_while_they_answer():
    ask = _ask_section()
    assert "Keep drawing the brief out while they are still answering" in ask
    assert "each round goes deeper than the last" in ask
    assert "never repeats what they have told you" in ask


def test_it_may_not_stop_because_the_brief_reads_well():
    ask = _ask_section()
    assert "Do NOT stop because the brief reads well" in ask
    assert "If the brief is already specific" not in ask, (
        "the old bias told the chat to stop asking whenever the brief looked "
        "specific -- that is how a brief with no country reached the writer"
    )


def test_the_customer_ends_the_questions_not_the_chat():
    ask = _ask_section()
    assert "The customer ends the questions, not you" in ask
    for phrase in ("build now", "just build it", "go", "start", "skip", "you decide", "enough"):
        assert phrase in ask, phrase
    assert "call draft_platform on that turn and ask nothing more" in ask


def test_build_now_still_wins_immediately():
    """The escape hatch is the whole contract: no interrogation."""
    ask = _ask_section()
    stop = ask.index("The customer ends the questions")
    assert "ask nothing more" in ask[stop:]


def test_a_pending_blueprint_or_a_running_build_still_stops_questions():
    ask = _ask_section()
    assert "Never ask_user when a blueprint is pending or a coding run exists" in ask
    assert "at zero you must draft" in ask


def test_it_still_asks_about_what_the_writer_would_otherwise_invent():
    ask = _ask_section()
    for subject in ("country", "currency", "documents", "roles", "formulas"):
        assert subject in ask, subject
