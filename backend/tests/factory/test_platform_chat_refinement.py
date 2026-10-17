"""Platform chat flow refinement commands and deterministic vertical extraction."""

from __future__ import annotations

import pytest

from app.factory import platform_chat_flow
from app.factory.floor_actions import FloorAction
from app.factory.platform_chat_flow import apply_refinement
from app.models.session import ProductDesignState, SessionState


@pytest.fixture
def state():
    s = SessionState(session_id="sess-refine", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    return s


def _draft_retail(state):
    from app.factory.product_architect import draft_blueprint_from_brief

    # The vertical is the Floor's structured hint, not parsed from the brief.
    bp = draft_blueprint_from_brief(
        "build me secure multi users platform for my retail business",
        vertical_hint="retail",
    )
    state.product_design.blueprint = bp.model_dump(mode="json")
    return bp


def test_refinement_add_capability(state):
    _draft_retail(state)
    result = apply_refinement(state, FloorAction.ADD_CAPABILITY, "vector_search")
    assert result["ok"] is True
    assert result["refined"] is True
    cap_ids = [c["id"] for c in result["blueprint"]["capabilities"]]
    assert "vector_search" in cap_ids
    vs = next(c for c in result["blueprint"]["capabilities"] if c["id"] == "vector_search")
    assert vs["strategy_hint"] == "REUSE"
    assert vs["block_ids"] == ["vector_search"]


def test_refinement_add_unknown_capability_becomes_generate(state):
    _draft_retail(state)
    result = apply_refinement(state, FloorAction.ADD_CAPABILITY, "loyalty_rewards")
    assert result["ok"] is True
    cap = next(c for c in result["blueprint"]["capabilities"] if c["id"] == "loyalty_rewards")
    assert cap["strategy_hint"] == "GENERATE"
    assert cap["block_ids"] == []


def test_refinement_remove_capability(state):
    _draft_retail(state)
    before = [c["id"] for c in state.product_design.blueprint["capabilities"]]
    assert "audit" in before
    result = apply_refinement(state, FloorAction.REMOVE_CAPABILITY, "audit")
    assert result["ok"] is True
    after = [c["id"] for c in result["blueprint"]["capabilities"]]
    assert "audit" not in after


def test_refinement_cannot_remove_last_capability(state):
    _draft_retail(state)
    # remove audit first
    apply_refinement(state, FloorAction.REMOVE_CAPABILITY, "audit")
    # now only retail_core remains
    result = apply_refinement(state, FloorAction.REMOVE_CAPABILITY, "retail_core")
    assert result["ok"] is False


def test_refinement_list_capabilities(state):
    _draft_retail(state)
    result = apply_refinement(state, FloorAction.LIST_CAPABILITIES)
    assert result["ok"] is True
    assert "retail_core" in result["summary"]


def test_refinement_rename_product(state):
    _draft_retail(state)
    result = apply_refinement(state, FloorAction.RENAME, "Acme Retail Hub")
    assert result["ok"] is True
    assert result["blueprint"]["product_name"] == "Acme Retail Hub"


def test_refinement_set_vertical(state):
    _draft_retail(state)
    result = apply_refinement(state, FloorAction.SET_VERTICAL, "boutique_retail")
    assert result["ok"] is True
    assert result["blueprint"]["vertical"] == "boutique_retail"
    assert result["blueprint"]["product_id"] == "boutique_retail"


def test_vertical_is_the_structured_hint_never_the_brief_prose():
    """A brief's words name no vertical; the Floor's hint does."""
    from app.factory import product_architect

    bp = product_architect._draft_blueprint_from_brief_inner(
        "build me a zorblat management platform for my quux business", use_llm=False
    )
    assert bp.vertical == "product"
    hinted = product_architect._draft_blueprint_from_brief_inner(
        "build me a platform", vertical_hint="zorblat_yards", use_llm=False
    )
    assert hinted.vertical == "zorblat_yards"


def test_no_text_is_parsed_into_a_refinement(state):
    """Refinements arrive typed. There is no parser of command wording left."""
    assert not hasattr(platform_chat_flow, "parse_refinement_command")
    assert not hasattr(platform_chat_flow, "refine_from_chat")


def test_a_refinement_without_its_value_is_refused(state):
    _draft_retail(state)
    result = apply_refinement(state, FloorAction.ADD_CAPABILITY, "")
    assert result["ok"] is False and result["refined"] is False


# The build level is the user's typed INTAKE (set_build_level / confirm_intake),
# not a blueprint refinement: tests/factory/test_build_level.py.


def test_the_build_level_is_not_a_refinement(state):
    _draft_retail(state)
    from app.factory.floor_actions import FloorActionError

    with pytest.raises(FloorActionError):
        apply_refinement(state, FloorAction.SET_BUILD_LEVEL, "prototype")


def test_add_and_remove_stay_themselves(state):
    _draft_retail(state)
    assert apply_refinement(state, FloorAction.ADD_CAPABILITY, "vector_search")["action"] == "add_capability"
    r = apply_refinement(state, FloorAction.REMOVE_CAPABILITY, "audit")
    assert r["action"] == "remove_capability"
