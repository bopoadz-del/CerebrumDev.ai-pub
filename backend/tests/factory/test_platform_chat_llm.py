"""Floor chat LLM: free text in, a TYPED decision out -- never a build.

The coding agent starts only from the Floor's typed Approve / Continue
action. A model that answers start_coder gets the user pointed at the
control; a model miss gets an honest pointer too -- no prose fallback.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.factory import platform_chat_flow, platform_chat_llm
from app.factory.product_architect import LlmSoftMiss
from app.models.session import ProductDesignState, SessionState


def _state_with_pending_blueprint() -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    s = SessionState(session_id="sess-chat-llm", user_id="user-1", account_id="acct-1")
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
        lines = [line for line in raw.strip().splitlines() if line]
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


def test_chat_llm_off_without_key(monkeypatch):
    monkeypatch.delenv(platform_chat_llm.CHAT_LLM_ENV, raising=False)
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "CEREBRUM_CHAT_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    assert platform_chat_llm.chat_llm_enabled() is False


def test_chat_llm_on_when_key_present(monkeypatch):
    monkeypatch.delenv(platform_chat_llm.CHAT_LLM_ENV, raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "sk-test-not-used")
    assert platform_chat_llm.chat_llm_enabled() is True


def test_chat_llm_uses_cerebrum_chat_when_provider_is_cursor(monkeypatch):
    """Live shape: LLM_PROVIDER=cursor + CURSOR_API_KEY + CEREBRUM_CHAT_*."""
    monkeypatch.delenv(platform_chat_llm.CHAT_LLM_ENV, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "cursor")
    monkeypatch.setenv("CURSOR_API_KEY", "crsr-ba-only")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_API_KEY", "chat-key-live")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_BASE_URL", "https://chat.example.test/v1")
    monkeypatch.setenv("CEREBRUM_CHAT_LLM_MODEL", "chat-model-live")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert platform_chat_llm.chat_llm_enabled() is True
    from app.core.llm_config import get_llm_config

    cfg = get_llm_config()
    assert cfg["api_key"] == "chat-key-live"
    assert "api.cursor.com" not in cfg["base_url"]


def test_looks_good_still_orchestrates(monkeypatch, session):
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    assert platform_chat_llm.should_orchestrate(session, "looks good") is True


def test_business_brief_orchestrates_without_saying_platform(monkeypatch):
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    state = SessionState(session_id="sess-brief", user_id="user-1", account_id="acct-1")
    state.product_design = ProductDesignState()
    assert (
        platform_chat_llm.should_orchestrate(
            state, "build me a tasting room for a family winery"
        )
        is True
    )


def test_model_start_coder_points_at_approve_and_never_builds(session, monkeypatch):
    started = []
    monkeypatch.setattr(
        platform_chat_flow, "approve_and_generate", lambda *a, **k: started.append(1) or {}
    )
    monkeypatch.setattr(
        platform_chat_flow, "start_or_resume_coder", lambda *a, **k: started.append(1) or {}
    )
    result = platform_chat_llm.apply_decision(session, "approve", {"action": "start_coder"})
    assert started == []
    assert result["sse"] == "info"
    assert result["awaiting_action"] == "approve"


def test_any_free_text_is_the_models_to_decide(monkeypatch, session):
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    for text in ("approve", "add block vector_search to the chain", "looks good"):
        assert platform_chat_llm.should_orchestrate(session, text) is True


def test_decide_empty_object_is_soft_miss(session, monkeypatch):
    monkeypatch.setattr(platform_chat_llm, "_llm_json_call", lambda messages: {})
    with pytest.raises(LlmSoftMiss, match="empty action"):
        platform_chat_llm.decide(session, "looks good")


def test_decide_empty_action_is_soft_miss(session, monkeypatch):
    monkeypatch.setattr(platform_chat_llm, "_llm_json_call", lambda messages: {"action": ""})
    with pytest.raises(LlmSoftMiss, match="empty action"):
        platform_chat_llm.decide(session, "looks good")


def test_decide_unknown_nonempty_action_is_valueerror(session, monkeypatch):
    monkeypatch.setattr(
        platform_chat_llm, "_llm_json_call", lambda messages: {"action": "explode"}
    )
    with pytest.raises(ValueError, match="unknown action 'explode'") as excinfo:
        platform_chat_llm.decide(session, "hi")
    assert not isinstance(excinfo.value, LlmSoftMiss)


def test_try_decide_soft_miss_logs_warning_not_error(session, monkeypatch, caplog):
    monkeypatch.setattr(platform_chat_llm, "_llm_json_call", lambda messages: {})
    with caplog.at_level(logging.WARNING, logger="app.factory.platform_chat_llm"):
        assert platform_chat_llm.try_decide(session, "looks good") is None
    assert "Floor chat LLM miss" in caplog.text
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_try_decide_hard_failure_still_logs_exception(session, monkeypatch, caplog):
    def boom(messages):
        raise ValueError("Floor chat LLM returned unknown action 'explode'")

    monkeypatch.setattr(platform_chat_llm, "_llm_json_call", boom)
    with caplog.at_level(logging.ERROR, logger="app.factory.platform_chat_llm"):
        assert platform_chat_llm.try_decide(session, "hi") is None
    assert "Floor chat LLM failed" in caplog.text


@pytest.mark.asyncio
async def test_typed_approve_skips_llm_and_starts_coder(session, monkeypatch):
    called = {"decide": 0}

    def boom(state, message):
        called["decide"] += 1
        raise AssertionError("a typed approve must not call the chat LLM")

    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    monkeypatch.setattr(platform_chat_llm, "decide", boom)

    def fake_approve(state, output_root=None, triggered_by="regex_approve"):
        return {
            "ok": True,
            "summary": "coding agent has taken over",
            "generation": {"engine": "runner", "product_id": "retail", "triggered_by": triggered_by},
            "triggered_by": triggered_by,
        }

    monkeypatch.setattr(platform_chat_flow, "approve_and_generate", fake_approve)
    session.product_design.build_level = "pilot"  # the user's typed choice
    events = await _collect_events(session.session_id, "", action="approve")
    assert called["decide"] == 0
    kinds = [e["event"] for e in events]
    assert kinds.count("generation") == 1
    assert "chain" not in kinds
    payload = json.loads(next(e["data"] for e in events if e["event"] == "generation"))
    assert payload["generation"]["engine"] == "runner"


@pytest.mark.asyncio
async def test_chat_llm_looks_good_does_not_build(session, monkeypatch):
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    monkeypatch.setattr(
        platform_chat_llm,
        "decide",
        lambda state, message: {
            "action": "start_coder",
            "brief": "",
            "refine": {"op": "", "value": ""},
            "message": "",
        },
    )
    started = []
    monkeypatch.setattr(
        platform_chat_flow, "approve_and_generate", lambda *a, **k: started.append(1) or {}
    )
    events = await _collect_events(session.session_id, "looks good")
    kinds = [e["event"] for e in events]
    assert started == []
    assert "generation" not in kinds


@pytest.mark.asyncio
async def test_model_miss_never_falls_back_to_a_prose_approve(session, monkeypatch, caplog):
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    monkeypatch.setattr(
        platform_chat_llm,
        "decide",
        lambda state, message: (_ for _ in ()).throw(LlmSoftMiss("empty action")),
    )
    started = []
    monkeypatch.setattr(
        platform_chat_flow, "approve_and_generate", lambda *a, **k: started.append(1) or {}
    )
    with caplog.at_level(logging.WARNING, logger="app.factory.platform_chat_llm"):
        events = await _collect_events(session.session_id, "approve")
    assert started == []
    assert "generation" not in [e["event"] for e in events]
    assert "Floor chat LLM miss" in caplog.text


@pytest.mark.asyncio
async def test_running_build_coder_owns_the_floor(session, monkeypatch):
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda state: True)
    monkeypatch.setattr(
        platform_chat_flow,
        "running_build_reply",
        lambda state: {
            "ok": True,
            "sse": "info",
            "summary": "The coding agent has taken over. It is writing retail.",
            "stream_delta": True,
        },
    )
    events = await _collect_events(session.session_id, "how is the build going")
    kinds = [e["event"] for e in events]
    assert "chain" not in kinds
    assert "status" not in kinds
    text = " ".join(e["data"] for e in events if e["event"] == "delta")
    assert "coding agent has taken over" in " ".join(text.split()).lower()


@pytest.mark.asyncio
async def test_feature_list_exclusion_still_refines_before_llm(session, monkeypatch):
    """Approve & build unticks send a typed remove_capability -- that must
    not wait on the chat LLM, or the coder would start with the wrong list."""
    called = {"decide": 0}

    def boom(state, message):
        called["decide"] += 1
        raise AssertionError("LLM must not see refinement commands")

    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    monkeypatch.setattr(platform_chat_llm, "decide", boom)
    events = await _collect_events(session.session_id, "", action="remove_capability", value="audit")
    assert called["decide"] == 0
    kinds = [e["event"] for e in events]
    assert "blueprint" in kinds
    assert "generation" not in kinds
