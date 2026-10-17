"""Factory-floor routing triage — retail build must never leak to the legacy
kit-chain generator, and card summaries must be emitted exactly once.

Covers the two live bugs from user testing:
(a) "Generated chain failed validation" on a standard retail request — the
    message fell through to the legacy chain path (either the intent regex
    missed the phrasing, or a pending blueprint + unrecognized message leaked
    through). The kernel refusal was correct; the routing was not.
(b) Doubled blueprint text — the summary was emitted both in the card event
    and again as a word-by-word delta stream appended to the same bubble.
"""

from __future__ import annotations

import json

import pytest

from app.factory import platform_chat_flow
from app.models.session import ProductDesignState, SessionState


# --- (a) routing is typed: no phrasing selects a path ------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "build a platform for retail",
        "approve",
        "go ahead, build it",
        "continue",
        "rename product to Zorblat",
        "add block vector_search to the chain",
    ],
)
async def test_free_text_never_picks_an_action(session, message):
    """With no typed action and no chat LLM, words decide nothing: no draft,
    no approval, no resume, no rename, no legacy chain -- an honest pointer
    at the Floor controls, and the pending blueprint untouched."""
    before = dict(session.product_design.blueprint)
    events = await _collect_events(session.session_id, message)
    kinds = [e["event"] for e in events]
    assert "blueprint" not in kinds and "generation" not in kinds
    assert "chain" not in kinds and "status" not in kinds
    assert session.product_design.blueprint == before
    assert session.product_design.blueprint_approved is False
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_the_chain_action_reaches_the_legacy_configurator(session, monkeypatch):
    from app.routers import chat as chat_router

    async def _boom(**kwargs):
        raise RuntimeError("zorblat chain generator reached")

    monkeypatch.setattr(chat_router, "generate_chain_suggestion", _boom)
    events = await _collect_events(session.session_id, "review this contract", action="chain")
    kinds = [e["event"] for e in events]
    assert kinds[0] == "status"  # the legacy path opens with status:thinking


# --- (b) + fallthrough: SSE stream contract ---------------------------------


def _state_with_pending_blueprint() -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    s = SessionState(session_id="sess-routing", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    bp = draft_blueprint_from_brief("build a platform for retail")
    s.product_design.blueprint = bp.model_dump(mode="json")
    return s


async def _collect_events(session_id: str, message: str, action=None, value=None):
    from app.factory.floor_actions import parse_action
    from app.routers import chat as chat_router

    events = []
    async for raw in chat_router._stream_response(
        session_id, message, parse_action(action), value
    ):
        lines = [l for l in raw.strip().splitlines() if l]
        ev = {"event": "", "data": ""}
        for line in lines:
            if line.startswith("event:"):
                ev["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line.split(":", 1)[1].strip())
        events.append(ev)
    return events


@pytest.fixture
def session(monkeypatch):
    from app.core import session_store

    state = _state_with_pending_blueprint()
    session_store._session_store[state.session_id] = state
    yield state
    session_store._session_store.pop(state.session_id, None)


@pytest.mark.asyncio
async def test_pending_blueprint_unrecognized_message_never_hits_legacy_chain(session):
    # Free text while a blueprint is pending (no chat LLM) gets an honest
    # pointer at the controls -- no legacy chain, no failed validation.
    events = await _collect_events(session.session_id, "make the theme dark please")
    kinds = [e["event"] for e in events]
    assert "error" not in kinds
    assert "chain" not in kinds
    assert "status" not in kinds  # legacy path emits status:thinking first
    text = " ".join(e["data"] for e in events if e["event"] == "delta")
    assert "approve" in text.lower()
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_blueprint_summary_emitted_exactly_once(session):
    # A typed Draft re-drafts: the summary must arrive ONLY in the blueprint
    # card event -- zero delta re-stream (that doubled the text).
    events = await _collect_events(session.session_id, "build a platform for retail", action="draft")
    kinds = [e["event"] for e in events]
    assert kinds.count("blueprint") == 1
    assert "delta" not in kinds
    bp = next(e["data"] for e in events if e["event"] == "blueprint")
    assert json.loads(bp)["summary"]


@pytest.mark.asyncio
async def test_approval_generation_emitted_exactly_once(session, monkeypatch):
    def fake_approve(state, **kwargs):
        return {"ok": True, "summary": "Platform generated.", "product_id": "p1"}

    monkeypatch.setattr(platform_chat_flow, "approve_and_generate", fake_approve)
    session.product_design.build_level = "pilot"  # the user's typed choice
    events = await _collect_events(session.session_id, "", action="approve")
    kinds = [e["event"] for e in events]
    assert kinds.count("generation") == 1
    assert "delta" not in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("llm_says_start", [False, True])
async def test_approve_in_text_does_not_build(session, monkeypatch, llm_says_start):
    """No approve -> no build. A chat message that SAYS 'approve' carries no
    typed approve action, so it must not start a build -- with no chat model,
    and with a chat model that answers start_coder."""
    from app.factory import platform_chat_llm

    started = []

    def _approve(*a, **k):
        started.append("approve")
        return {}

    def _resume(*a, **k):
        started.append("resume")
        return {}

    monkeypatch.setattr(platform_chat_flow, "approve_and_generate", _approve)
    monkeypatch.setattr(platform_chat_flow, "start_or_resume_coder", _resume)
    if llm_says_start:
        monkeypatch.setattr(platform_chat_llm, "should_orchestrate", lambda *a, **k: True)
        monkeypatch.setattr(
            platform_chat_llm,
            "try_decide_with_reason",
            lambda *a, **k: (
                {
                    "action": "start_coder",
                    "brief": "",
                    "message": "",
                    "refine": {"op": "", "value": ""},
                    "connectors": [],
                    "missing_connectors": [],
                },
                None,
            ),
        )
    events = await _collect_events(session.session_id, "approve - please approve and build it")
    kinds = [e["event"] for e in events]
    assert started == []
    assert "generation" not in kinds
    assert session.product_design.blueprint_approved is False
    assert kinds[-1] == "done"
