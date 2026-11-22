"""Platform intake is never refused by matching the user's words.

The scope gate (``app.core.grounding.check_scope_refusal``) was written for a
surface where the Factory itself answers a question. It ran on every Floor
message, ahead of the platform flow. Each pattern pairs two word lists across
the whole message, so an ordinary brief -- an HR one that says "approve" and
later "column", an insurance one with "file" and "claim" -- was answered
"I can't help with that (out_of_scope)" and never reached the Floor chat model.

The gate now runs only where the Factory's own model answers: the kit-chain
configurator (the typed ``chain`` action, or free text while the platform flow
is switched off). On platform intake a message is declined only by the chat
model's typed decision.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

import app.routers.chat as chat_router
from app.core import grounding
from app.factory import platform_chat_flow, platform_chat_llm
from app.factory.floor_actions import FloorAction
from app.models.session import ProductDesignState, SessionState

#: Ordinary product briefs. Each one is matched by a default scope pattern
#: (asserted in the tests, so a brief that stops matching cannot pass quietly).
HR_BRIEF = (
    "Build an HR platform. Admins approve leave requests. "
    "Add a status column to the leave table."
)
INSURANCE_BRIEF = (
    "An insurance platform: the customer can upload a file for each incident "
    "and an adjuster reviews each claim."
)
CLINIC_BRIEF = (
    "A clinic platform that keeps each patient record, the medication list "
    "and the dosing schedule the doctor set."
)
FACILITIES_BRIEF = (
    "A facilities platform: staff log an emergency call and the duty manager "
    "is paged immediately."
)
BRIEFS = [HR_BRIEF, INSURANCE_BRIEF, CLINIC_BRIEF, FACILITIES_BRIEF]

#: Questions that ask the Factory itself for a professional judgement.
DOSING_QUESTION = "What morphine dose should I administer to the patient?"
SIGNOFF_QUESTION = "Can you certify this beam as adequate for sign-off?"

SESSION_ID = "sess-intake-no-word-gate"


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A fresh Floor session, the default scope table, the platform flow on."""
    from app.core import session_store
    from app.factory import store_catalog

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.delenv("PLATFORM_CHAT_FLOW_ENABLED", raising=False)
    # The scope table is cached per process and can be replaced from a file:
    # these tests are about the table the Factory ships.
    monkeypatch.delenv("CEREBRUM_SCOPE_REFUSALS_PATH", raising=False)
    monkeypatch.setattr(grounding, "_SCOPE_CACHE", None)
    # The real catalog walks the Store with git; these tests are about routing.
    store_catalog.clear_cache()
    monkeypatch.setattr(
        store_catalog,
        "store_catalog",
        lambda *a, **k: {"blocks": [], "connectors": [], "mcp": [], "not_cleared": [], "kits": []},
    )
    state = SessionState(session_id=SESSION_ID, user_id="user-1", account_id="acct-1")
    state.product_design = ProductDesignState()
    session_store._session_store[state.session_id] = state
    yield state
    session_store._session_store.pop(state.session_id, None)


def _chat_model_answers(monkeypatch, decision: Dict[str, Any]) -> List[str]:
    """The Floor chat model returns ``decision``; capture what it was sent."""
    sent: List[str] = []

    def fake(messages):
        sent.append(messages[-1]["content"])
        return dict(decision)

    monkeypatch.setenv(platform_chat_llm.CHAT_LLM_ENV, "1")
    monkeypatch.setattr(platform_chat_llm, "_llm_json_call", fake)
    return sent


def _capture_draft(monkeypatch) -> List[str]:
    """Stand in for the architect; capture the brief it was asked to draft."""
    briefs: List[str] = []

    def fake_draft(state, brief):
        briefs.append(brief)
        state.product_design.blueprint = {"product_name": "X", "capabilities": []}
        return {"ok": True, "summary": "Blueprint drafted: X"}

    monkeypatch.setattr(platform_chat_flow, "draft_from_chat", fake_draft)
    return briefs


async def _collect_events(session_id: str, message: str, action=None, value=None):
    from app.factory.floor_actions import parse_action

    events = []
    async for raw in chat_router._stream_response(
        session_id, message, parse_action(action), value
    ):
        ev = {"event": "", "data": ""}
        for line in (ln for ln in raw.strip().splitlines() if ln):
            if line.startswith("event:"):
                ev["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line.split(":", 1)[1].strip())
        events.append(ev)
    return events


def _kinds(events) -> List[str]:
    return [e["event"] for e in events]


def _streamed_text(events) -> str:
    return "".join(e["data"] for e in events if e["event"] == "delta").strip()


def _info(events) -> Dict[str, Any]:
    return json.loads(next(e["data"] for e in events if e["event"] == "info"))


def _refused_by_the_word_gate(events) -> bool:
    return grounding.VERDICT_OUT_OF_SCOPE in json.dumps(events)


def _scope_verdicts() -> List[Dict[str, Any]]:
    log = grounding.verdict_log_path()
    if not log.is_file():
        return []
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r.get("verdict") == grounding.VERDICT_OUT_OF_SCOPE]


# --- platform intake: the words decide nothing --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("brief", BRIEFS)
async def test_a_brief_the_scope_patterns_match_reaches_the_floor_decision(
    session, monkeypatch, brief
):
    assert grounding.check_scope_refusal(brief) is not None, "the brief must match a pattern"
    question = "Which country do you operate in, and in which currency?"
    sent = _chat_model_answers(monkeypatch, {"action": "ask_user", "message": question})

    events = await _collect_events(session.session_id, brief)

    # The Floor chat model was asked, with the user's own words.
    assert len(sent) == 1
    assert brief in sent[0]
    # Its typed decision is what the user gets -- not a refusal by pattern.
    assert not _refused_by_the_word_gate(events)
    assert _info(events)["summary"].endswith(question)
    assert _streamed_text(events).endswith(question)
    assert session.product_design.elicitation_rounds == 1
    assert _scope_verdicts() == []
    assert session.chat_history[0] == {"role": "user", "content": brief}
    assert _kinds(events)[-1] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("brief", BRIEFS)
async def test_a_matched_brief_is_drafted_when_the_chat_decision_is_to_draft(
    session, monkeypatch, brief
):
    assert grounding.check_scope_refusal(brief) is not None, "the brief must match a pattern"
    restated = "The customer wants: " + brief
    sent = _chat_model_answers(monkeypatch, {"action": "draft_platform", "brief": restated})
    briefs = _capture_draft(monkeypatch)

    events = await _collect_events(session.session_id, brief)

    assert len(sent) == 1
    assert brief in sent[0]
    assert briefs == [restated], "the decision's draft is applied"
    assert "blueprint" in _kinds(events)
    assert not _refused_by_the_word_gate(events)
    assert _scope_verdicts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("brief", [HR_BRIEF, CLINIC_BRIEF])
async def test_a_typed_draft_of_a_matched_brief_reaches_the_drafter(session, monkeypatch, brief):
    """The Draft control sends the composer text as ``message``: also intake."""
    assert grounding.check_scope_refusal(brief) is not None, "the brief must match a pattern"
    briefs = _capture_draft(monkeypatch)

    events = await _collect_events(session.session_id, brief, action="draft")

    assert briefs == [brief]
    assert _kinds(events) == ["blueprint", "done"]
    assert not _refused_by_the_word_gate(events)
    assert _scope_verdicts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("brief", [INSURANCE_BRIEF, FACILITIES_BRIEF])
async def test_a_matched_brief_with_no_chat_model_gets_the_controls_not_a_refusal(
    session, monkeypatch, brief
):
    assert grounding.check_scope_refusal(brief) is not None, "the brief must match a pattern"
    monkeypatch.setenv(platform_chat_llm.CHAT_LLM_ENV, "0")

    events = await _collect_events(session.session_id, brief)

    assert not _refused_by_the_word_gate(events)
    assert _info(events)["ok"] is True
    assert _scope_verdicts() == []
    assert [m["role"] for m in session.chat_history] == ["user", "assistant"]
    assert session.chat_history[0]["content"] == brief


# --- a refusal on the Floor is the chat decision's typed outcome ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, decline",
    [
        (DOSING_QUESTION, "I build software platforms; I cannot advise on a dose."),
        (SIGNOFF_QUESTION, "That needs a licensed engineer. Describe a platform to build."),
    ],
)
async def test_a_typed_decline_from_the_chat_decision_is_honoured(
    session, monkeypatch, message, decline
):
    sent = _chat_model_answers(monkeypatch, {"action": "reply", "message": decline})
    briefs = _capture_draft(monkeypatch)

    events = await _collect_events(session.session_id, message)

    assert len(sent) == 1
    # The decision's own words are the answer, and nothing is drafted or built.
    assert _info(events)["summary"] == decline
    assert _streamed_text(events) == decline
    assert briefs == []
    assert session.product_design.blueprint is None
    assert "blueprint" not in _kinds(events) and "generation" not in _kinds(events)
    assert session.chat_history == [
        {"role": "user", "content": message},
        {"role": "assistant", "content": decline},
    ]
    assert not _refused_by_the_word_gate(events)


# --- the configurator answers questions itself: the gate still guards it -------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question, action, platform_flow",
    [
        (DOSING_QUESTION, "chain", None),
        (SIGNOFF_QUESTION, "chain", None),
        (DOSING_QUESTION, None, "off"),
        (SIGNOFF_QUESTION, None, "0"),
    ],
)
async def test_the_configurator_still_refuses_a_matched_question(
    session, monkeypatch, question, action, platform_flow
):
    if platform_flow is not None:
        monkeypatch.setenv("PLATFORM_CHAT_FLOW_ENABLED", platform_flow)
    rule = grounding.check_scope_refusal(question)
    assert rule is not None
    llm = AsyncMock(return_value={"message": "must never run", "chain": None, "rules": []})
    monkeypatch.setattr(chat_router, "generate_chain_suggestion", llm)
    sent = _chat_model_answers(monkeypatch, {"action": "reply", "message": "must never run"})

    events = await _collect_events(session.session_id, question, action=action)

    notice = "I can't help with that: " + rule["reason"] + " (out_of_scope)"
    assert llm.await_count == 0, "a refused question must never reach the LLM"
    assert sent == [], "the configurator is not the Floor chat model's turn"
    assert _streamed_text(events) == notice
    assert set(_kinds(events)) == {"delta", "done"}
    assert session.chat_history == [
        {"role": "user", "content": question},
        {"role": "assistant", "content": notice},
    ]
    verdicts = _scope_verdicts()
    assert len(verdicts) == 1
    assert verdicts[0]["refusal_id"] == rule["id"]
    assert verdicts[0]["surface"] == "chat"
    assert verdicts[0]["query"] == question
    assert verdicts[0]["session_id"] == session.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    ["When is the next fleet service due?", "Which document should I upload first?"],
)
async def test_the_configurator_answers_a_question_no_pattern_matches(
    session, monkeypatch, question
):
    assert grounding.check_scope_refusal(question) is None
    answer = "I suggest starting with the intake capability."
    llm = AsyncMock(return_value={"message": answer, "chain": None, "rules": []})
    monkeypatch.setattr(chat_router, "generate_chain_suggestion", llm)

    events = await _collect_events(session.session_id, question, action="chain")

    assert llm.await_count == 1
    assert _streamed_text(events) == answer
    assert not _refused_by_the_word_gate(events)
    assert _scope_verdicts() == []


# --- which turns the configurator answers -------------------------------------


@pytest.mark.parametrize(
    "action, platform_flow, expected",
    [
        (FloorAction.CHAIN, None, True),
        (FloorAction.CHAIN, "off", True),
        (None, "off", True),
        (None, "false", True),
        (None, None, False),
        (None, "true", False),
        (FloorAction.DRAFT, "off", False),
        (FloorAction.APPROVE, None, False),
    ],
)
def test_only_the_chain_action_or_a_switched_off_platform_flow_is_the_configurator(
    monkeypatch, action, platform_flow, expected
):
    if platform_flow is None:
        monkeypatch.delenv("PLATFORM_CHAT_FLOW_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_CHAT_FLOW_ENABLED", platform_flow)
    assert chat_router._is_configurator_turn(action) is expected


# --- no session: an error on every surface, before any gate -------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, action",
    [(HR_BRIEF, None), (DOSING_QUESTION, "chain")],
)
async def test_a_missing_session_is_an_error_before_anything_else(monkeypatch, message, action):
    monkeypatch.setattr(chat_router, "get_session", lambda session_id: None)

    events = await _collect_events("sess-that-does-not-exist", message, action=action)

    assert events == [{"event": "error", "data": "Session not found"}]
