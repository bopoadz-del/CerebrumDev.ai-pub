"""Tests for the chat-driven platform creation bridge.

The bridge routes Floor chat onto the existing product_design state machine
(same functions as routers/session_product.py). Actions arrive TYPED
(app.factory.floor_actions); no function reads the user's words to pick one.
These tests cover the typed contract and the draft -> approve -> generate
flow. PLATFORM_CHAT_FLOW_ENABLED=off restores the legacy configurator.
"""

from __future__ import annotations

import pytest

from app.factory import platform_chat_flow
from app.models.session import SessionState


# --- Typed actions: the Floor says what to do; the words never do -------------

from app.factory.floor_actions import FloorAction, FloorActionError, parse_action  # noqa: E402


@pytest.mark.parametrize("action", [a.value for a in FloorAction])
def test_every_floor_action_parses_to_itself(action):
    assert parse_action(action) is FloorAction(action)


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_no_action_means_free_text(raw):
    assert parse_action(raw) is None


@pytest.mark.parametrize("raw", ["approve it", "go ahead", "zorblat"])
def test_an_unknown_action_is_refused_not_guessed(raw):
    with pytest.raises(FloorActionError):
        parse_action(raw)


def test_no_prose_routing_function_survives():
    for gone in (
        "is_platform_intent",
        "is_approval",
        "is_exact_approve_gate",
        "is_resume_request",
        "is_pilot_request",
        "is_kit_config_vocabulary",
        "is_explicit_platform_command",
        "should_handle_platform_message",
    ):
        assert not hasattr(platform_chat_flow, gone), gone


# --- The env gate still selects the legacy configurator ----------------------

def test_platform_chat_gate_defaults_on(monkeypatch):
    # Doctrine: the chat's purpose is building platforms -- default ON.
    monkeypatch.delenv("PLATFORM_CHAT_FLOW_ENABLED", raising=False)
    assert platform_chat_flow.platform_chat_enabled()


def test_platform_chat_gate_honors_explicit_off(monkeypatch):
    monkeypatch.setenv("PLATFORM_CHAT_FLOW_ENABLED", "off")
    assert not platform_chat_flow.platform_chat_enabled()


# --- Flow: draft -> pending -> approve -> generate ---------------------------

def _fresh_state() -> SessionState:
    return SessionState(session_id="test-session-chat-platform", user_id="test-user")


def test_draft_from_chat_parks_blueprint_on_session():
    state = _fresh_state()
    result = platform_chat_flow.draft_from_chat(
        state, "build me a platform for private estates"
    )
    assert result["ok"] is True
    assert state.product_design.blueprint is not None
    assert state.product_design.blueprint_approved is False
    assert state.product_design.generation is None
    assert platform_chat_flow.has_pending_blueprint(state)
    # Words do not pick a golden; this draft overlaps none by structure.
    assert result["source"] == "drafted"


def test_approve_and_generate_produces_product(tmp_path):
    state = _fresh_state()
    platform_chat_flow.draft_from_chat(
        state, "build me a platform for private estates"
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is True
    assert state.product_design.blueprint_approved is True
    assert state.product_design.plan is not None
    assert state.product_design.generation is not None
    output_dir = tmp_path / state.product_design.generation["product_id"]
    assert output_dir.exists()
    assert any(output_dir.iterdir()), "generated product dir must not be empty"
    assert not platform_chat_flow.has_pending_blueprint(state)  # approved now


def test_approve_lint_failure_never_opens_the_session(tmp_path, monkeypatch):
    """Approve compiles+lints; a rejected brief does not start generate."""
    state = _fresh_state()
    platform_chat_flow.draft_from_chat(
        state, "build me a platform for private estates"
    )

    class _Bad:
        ok = False
        errors = ["unresolved block id: planted"]

        def to_dict(self):
            return {"ok": False, "errors": self.errors}

    monkeypatch.setattr(
        platform_chat_flow,
        "_compile_and_lint_approved",
        lambda state, bp: {
            "lint": _Bad(),
            "plan": type("P", (), {"to_dict": lambda self: {}})(),
            "plain_language": "",
        },
    )
    started = []
    monkeypatch.setattr(
        platform_chat_flow,
        "generate_product",
        lambda *a, **k: started.append(1) or {"ok": True},
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)
    assert result["ok"] is False
    assert "BRIEF_LINT_REJECTED" in result["summary"]
    assert started == []
    assert state.product_design.generation is None


def test_approve_without_draft_raises():
    state = _fresh_state()
    with pytest.raises(ValueError, match="no blueprint"):
        platform_chat_flow.approve_and_generate(state, output_root=None)


def test_draft_resets_previous_design():
    state = _fresh_state()
    platform_chat_flow.draft_from_chat(state, "build me a platform for hotels")
    platform_chat_flow.draft_from_chat(state, "create a platform for clinics")
    # second draft replaces the first, approval flag reset
    assert state.product_design.blueprint_approved is False
    assert state.product_design.generation is None
    assert state.product_design.brief == "create a platform for clinics"


# --- F-4a (H1): the chat-flow draft surfaces the golden OFFER -----------------

def _chat_draft_with_threshold(monkeypatch, min_overlap: str, sid: str):
    """Draft through platform_chat_flow.draft_from_chat with the same brief and
    vertical hint the steward offer flow uses; only the kernel threshold
    differs, so the two calls are two inputs giving two results."""
    from tests.factory.blocks_root import real_blocks_root

    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(real_blocks_root()))
    monkeypatch.setenv("FACTORY_STAGE_RESUME", "0")
    monkeypatch.setenv("FACTORY_GOLDEN_MIN_OVERLAP", min_overlap)
    state = SessionState(session_id=sid, user_id="test-user")
    # The vertical is the user's Floor choice persisted on the session.
    state.product_design.vertical = "estate"
    result = platform_chat_flow.draft_from_chat(
        state, "Generate Projectsix private estate operations"
    )
    return state, result


def test_draft_from_chat_offers_golden_above_threshold_not_below(monkeypatch):
    """K2: draft_from_chat must return the blueprint's golden_suggestion.

    Above the lowered threshold the matching golden rides on
    result["golden_suggestion"]; at 0.99 it is None. In both the base stays
    the user's own draft (never substituted). Setting the returned key to None
    (or dropping it) reddens the above-threshold half."""
    state_hi, hi = _chat_draft_with_threshold(monkeypatch, "0.05", "chat-golden-hi")
    state_lo, lo = _chat_draft_with_threshold(monkeypatch, "0.99", "chat-golden-lo")

    assert hi["ok"] is True and lo["ok"] is True
    sug = hi["golden_suggestion"]
    assert sug is not None, "draft_from_chat must surface the golden offer"
    assert sug["golden_id"] == "projectsix"
    assert sug["drafting_mode"] == "golden"
    # The returned offer is the blueprint's own, not a separately built one.
    assert sug == state_hi.product_design.blueprint.get("golden_suggestion")
    assert lo["golden_suggestion"] is None
    assert hi["golden_suggestion"] != lo["golden_suggestion"]
    # The base is the user's draft in both cases.
    for state, res in ((state_hi, hi), (state_lo, lo)):
        assert res["blueprint"]["product_id"] == "estate"
        assert res["source"] == "drafted"
        assert state.product_design.blueprint["product_id"] == "estate"
