"""The Floor chat holds a conversation before it drafts.

The chat LLM used to be a four-way switch: it was sent the current message
alone -- no history -- and could only draft / start / refine / reply. A
first brief became a blueprint instantly, with nothing asked. From the
owner's side: "no conversation at all ... it is like hard wired".

Now it can ``ask_user`` first. It sees the conversation and the Store
inventory, the user's answers are folded verbatim into the brief the
architect drafts from, and the number of question rounds is capped in code
rather than left to the model.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.factory import platform_chat_flow, platform_chat_llm
from app.models.session import ProductDesignState, SessionState

BRIEF = "I run a small vet clinic and need a platform for it"


def _fresh_state() -> SessionState:
    s = SessionState(session_id="sess-elicit", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    return s


@pytest.fixture(autouse=True)
def _llm_on(monkeypatch):
    monkeypatch.setenv(platform_chat_llm.CHAT_LLM_ENV, "1")
    # The real catalog walks the Store with git; these tests are about the
    # conversation. test_the_catalog_is_derived_from_manifests builds its own.
    from app.factory import store_catalog as sc

    sc.clear_cache()
    monkeypatch.setattr(
        sc, "store_catalog",
        lambda *a, **k: {"blocks": [], "connectors": [], "mcp": [], "not_cleared": [], "kits": []},
    )


def _script(monkeypatch, replies: List[Dict[str, Any]]) -> List[str]:
    """Feed the orchestrator scripted model replies; capture what it was sent."""
    sent: List[str] = []
    queue = list(replies)

    def fake(messages):
        sent.append(messages[-1]["content"])
        return queue.pop(0)

    monkeypatch.setattr(platform_chat_llm, "_llm_json_call", fake)
    return sent


def _capture_draft(monkeypatch) -> List[str]:
    briefs: List[str] = []

    def fake_draft(state, brief):
        briefs.append(brief)
        state.product_design.blueprint = {"product_name": "X", "capabilities": []}
        return {"ok": True, "summary": "Blueprint drafted: X"}

    monkeypatch.setattr(platform_chat_flow, "draft_from_chat", fake_draft)
    return briefs


def _say(state: SessionState, message: str) -> Dict[str, Any]:
    """One user turn, recorded the way chat.py records it."""
    state.chat_history.append({"role": "user", "content": message})
    result = platform_chat_llm.try_handle(state, message)
    assert result is not None
    state.chat_history.append({"role": "assistant", "content": result["summary"]})
    return result


def test_a_first_brief_can_be_answered_with_questions_not_a_blueprint(monkeypatch):
    state = _fresh_state()
    briefs = _capture_draft(monkeypatch)
    _script(
        monkeypatch,
        [{"action": "ask_user", "message": "How many vets and front-desk staff will use it?"}],
    )

    result = _say(state, BRIEF)

    # No kit covers a vet clinic, so the factory's notice leads the question.
    assert result["summary"].endswith("How many vets and front-desk staff will use it?")
    assert result["sse"] == "info" and result.get("elicitation") is True
    assert briefs == [], "asking must not draft"
    assert state.product_design.blueprint is None
    assert state.product_design.elicitation_rounds == 1
    assert state.product_design.elicitation_turns == [BRIEF]


def test_the_model_is_shown_the_conversation_not_just_the_last_message(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    sent = _script(
        monkeypatch,
        [
            {"action": "ask_user", "message": "How many staff? Any documents to answer from?"},
            {"action": "draft_platform", "brief": "Vet clinic platform for 6 staff"},
        ],
    )

    _say(state, BRIEF)
    _say(state, "6 staff, and we have a drug dosage handbook as a PDF")

    second_call = sent[1]
    assert BRIEF in second_call, "the original brief was not in the model's context"
    assert "How many staff?" in second_call, "the Floor's own question was not in context"
    # The message being decided appears once, as the user message -- not
    # duplicated inside the transcript as well.
    assert second_call.count("drug dosage handbook") == 1


def test_answers_reach_the_architect_verbatim(monkeypatch):
    """A restatement is a summary; a summary can drop the answer that mattered."""
    state = _fresh_state()
    briefs = _capture_draft(monkeypatch)
    _script(
        monkeypatch,
        [
            {"action": "ask_user", "message": "How many staff?"},
            # The model's restatement forgets the handbook entirely.
            {"action": "draft_platform", "brief": "Vet clinic platform for 6 staff"},
        ],
    )

    _say(state, BRIEF)
    _say(state, "6 staff, and we have a drug dosage handbook as a PDF")

    assert len(briefs) == 1
    assert "Vet clinic platform for 6 staff" in briefs[0]
    assert BRIEF in briefs[0]
    assert "drug dosage handbook" in briefs[0], "the user's answer was lost to paraphrase"


def test_the_question_cap_is_enforced_in_code_not_by_the_model(monkeypatch):
    state = _fresh_state()
    briefs = _capture_draft(monkeypatch)
    ask = {"action": "ask_user", "message": "One more question?"}
    _script(monkeypatch, [dict(ask) for _ in range(platform_chat_llm.MAX_ELICITATION_ROUNDS + 1)])

    for i in range(platform_chat_llm.MAX_ELICITATION_ROUNDS):
        result = _say(state, f"answer {i}")
        assert result.get("elicitation") is True
    assert briefs == []

    # The model asks AGAIN. The session cannot afford it: it becomes a draft.
    _say(state, "final answer")

    assert len(briefs) == 1, "a model that keeps asking must not strand the user"
    for said in ("answer 0", "answer 1", "final answer"):
        assert said in briefs[0]


def test_the_model_is_told_how_many_rounds_remain(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    rounds = platform_chat_llm.MAX_ELICITATION_ROUNDS
    sent = _script(
        monkeypatch,
        [{"action": "ask_user", "message": f"q{n}"} for n in range(rounds)]
        + [{"action": "draft_platform", "brief": "b"}],
    )

    for turn in range(rounds + 1):
        _say(state, "m%d" % turn)

    assert f"Question rounds remaining: {rounds}." in sent[0]
    assert f"Question rounds remaining: {rounds - 1}." in sent[1]
    assert "ask_user is forbidden" in sent[rounds]


def test_drafting_resets_the_elicitation(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    _script(
        monkeypatch,
        [{"action": "ask_user", "message": "q"}, {"action": "draft_platform", "brief": "b"}],
    )

    _say(state, BRIEF)
    _say(state, "answer")

    assert state.product_design.elicitation_rounds == 0
    assert state.product_design.elicitation_turns == []


def test_no_questions_once_a_blueprint_is_pending(monkeypatch):
    """Questions belong before the first draft. After it: refine or approve."""
    state = _fresh_state()
    state.product_design.blueprint = {"product_name": "X", "capabilities": []}
    briefs = _capture_draft(monkeypatch)
    _script(monkeypatch, [{"action": "ask_user", "message": "another question?"}])

    result = _say(state, "make it better")

    assert result.get("elicitation") is not True
    assert len(briefs) == 1


def test_an_empty_question_is_not_a_turn(monkeypatch):
    state = _fresh_state()
    briefs = _capture_draft(monkeypatch)
    _script(monkeypatch, [{"action": "ask_user", "message": ""}])

    _say(state, BRIEF)

    assert len(briefs) == 1, "ask_user with nothing to ask must draft, not go silent"


def test_a_specific_brief_still_drafts_immediately(monkeypatch):
    """Elicitation is the model's choice, never a mandatory form."""
    state = _fresh_state()
    briefs = _capture_draft(monkeypatch)
    _script(monkeypatch, [{"action": "draft_platform", "brief": "A very specific brief"}])

    _say(state, "A very specific brief")

    assert briefs == ["A very specific brief"]
    assert state.product_design.elicitation_rounds == 0


def test_the_store_inventory_is_in_the_models_context(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    _catalog(monkeypatch)
    sent = _script(monkeypatch, [{"action": "reply", "message": "hi"}])

    _say(state, "do you have a google drive part?")

    assert "STORE INVENTORY (ready-made blocks): capture, notification." in sent[0]
    assert "NOT CLEARED FOR FACTORY BUILDS" in sent[0] and "google_drive" in sent[0]
    assert "KITS (deep, certified domain packs): platform." in sent[0]


def test_an_unreadable_inventory_never_blocks_the_chat(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    from app.factory import store_catalog as sc

    def boom(*a, **k):
        raise RuntimeError("store clone missing")

    monkeypatch.setattr(sc, "store_catalog", boom)
    sent = _script(monkeypatch, [{"action": "reply", "message": "hi"}])

    result = _say(state, "hello")

    assert result["summary"] == "hi"
    assert "do not claim any ready-made part" in sent[0]


def test_the_system_prompt_teaches_asking_and_inventory_honesty():
    prompt = " ".join(platform_chat_llm._SYSTEM.split())
    assert "ask_user" in prompt
    for topic in ("how many people", "documents", "size of the operation", "automatically"):
        assert topic in prompt, topic
    assert "just build it" in prompt, "the user must be able to skip the questions"
    assert "never invent an id" in prompt


# -- the Store: kits, connectors, MCP ------------------------------------------


def _real_draft_state(monkeypatch) -> SessionState:
    """A session whose draft is a real, validating blueprint."""
    from app.factory.product_architect import draft_blueprint_from_brief

    state = _fresh_state()

    def real_draft(st, brief):
        bp = draft_blueprint_from_brief("build a platform for retail")
        st.product_design.blueprint = bp.model_dump(mode="json")
        return {"ok": True, "summary": "Blueprint drafted.", "blueprint": st.product_design.blueprint}

    monkeypatch.setattr(platform_chat_flow, "draft_from_chat", real_draft)
    return state


def _catalog(monkeypatch, connectors=("notification",), mcp=(), not_cleared=("google_drive",), kits=("platform",)):
    from app.factory import store_catalog as sc

    cat = {
        "blocks": ["capture", *connectors, *mcp],
        "connectors": [{"id": c, "description": "", "tags": ["integration"]} for c in connectors],
        "mcp": [{"id": c, "description": "", "tags": ["mcp"]} for c in mcp],
        "not_cleared": [{"id": c, "description": "", "tags": ["integration"]} for c in not_cleared],
        "kits": list(kits),
    }
    monkeypatch.setattr(sc, "store_catalog", lambda *a, **k: cat)
    return cat


def test_an_attachable_connector_becomes_a_real_reuse_capability(monkeypatch):
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch)
    _script(monkeypatch, [{"action": "draft_platform", "brief": "b", "connectors": ["notification"]}])

    result = _say(state, "a shop platform that emails me on low stock")

    caps = state.product_design.blueprint["capabilities"]
    bound = [c for c in caps if "notification" in (c.get("block_ids") or [])]
    assert bound, "the chosen connector was not bound to the blueprint"
    assert bound[0]["strategy_hint"] == "REUSE"
    assert "Connectors from the store: notification." in result["summary"]
    assert result["blueprint"] == state.product_design.blueprint


def test_a_system_the_store_lacks_ships_as_a_marked_placeholder(monkeypatch):
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch)
    _script(
        monkeypatch,
        [{"action": "draft_platform", "brief": "b", "missing_connectors": ["Procore", "SAP S/4HANA"]}],
    )

    result = _say(state, "must sync with Procore and SAP")

    assert state.product_design.blueprint["connectors"] == ["procore", "sap_s_4hana"]
    assert "marked placeholders" in result["summary"]
    assert "procore" in result["summary"]


def test_the_models_connector_ids_are_not_trusted(monkeypatch):
    """A part that is not attachable -- invented, or in the store but not
    cleared -- must never be bound as if it were real."""
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch)
    _script(
        monkeypatch,
        [{"action": "draft_platform", "brief": "b", "connectors": ["google_drive", "made_up_erp"]}],
    )

    _say(state, "sync with google drive")

    bp = state.product_design.blueprint
    bound = {b for c in bp["capabilities"] for b in (c.get("block_ids") or [])}
    assert "google_drive" not in bound and "made_up_erp" not in bound
    assert bp["connectors"] == ["google_drive", "made_up_erp"], "demoted to honest placeholders"


def test_a_connector_the_architect_already_bound_is_not_duplicated(monkeypatch):
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch)
    _script(monkeypatch, [{"action": "draft_platform", "brief": "b", "connectors": ["notification"] * 2}])

    _say(state, "x")

    caps = state.product_design.blueprint["capabilities"]
    assert sum(1 for c in caps if "notification" in (c.get("block_ids") or [])) == 1


def test_the_models_words_survive_an_immediate_draft(monkeypatch):
    """That is where 'no top-notch kit, a simple one costs more' is said."""
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch)
    _script(
        monkeypatch,
        [{"action": "draft_platform", "brief": "b",
          "message": "We do not have a top-notch falconry kit; we can build a simple one."}],
    )

    result = _say(state, "falconry school platform, just build it")

    s = result["summary"]
    assert "We do not have a top-notch falconry kit; we can build a simple one." in s
    assert s.index("simple one.") < s.index("Blueprint drafted."), "model's words come first"


def test_an_uncertified_kit_for_the_chosen_vertical_is_named_not_denied(monkeypatch):
    """Live 2026-09-30: the shelf held a kit (uncertified) for the business and
    the notice still opened with the no-kit line -- absent and uncertified are
    different truths. The user CHOSE the vertical; the Store kit serving it is
    not declared ready, so the notice names that kit and how to build on it."""
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch, kits=("zorblat",))
    _domain_kits(monkeypatch, {"zorblat": {"serves_verticals": ["zorblat_yards"], "build_ready": False}})
    state.product_design.vertical = "zorblat_yards"
    _script(
        monkeypatch,
        [{"action": "ask_user", "message": "One question before drafting."}],
    )

    result = _say(state, "an operations platform for our yards")

    s2 = result["summary"]
    assert "zorblat" in s2, s2
    assert "uncertified" in s2 or "unverified" in s2, s2
    assert "don't have a top-notch kit" not in s2, (
        "a kit serves the chosen vertical; absence must not be claimed: " + s2
    )


def test_no_choice_means_the_no_kit_notice_whatever_the_message_names(monkeypatch):
    """No vertical chosen -> "product" -> no domain kit, so the honest no-kit
    notice -- even when the message names a kit that is on the shelf. The kit
    is never inferred from the user's words."""
    state = _real_draft_state(monkeypatch)
    _catalog(monkeypatch, kits=("zorblat",))
    _domain_kits(monkeypatch, {"zorblat": {"serves_verticals": ["zorblat_yards"], "build_ready": True}})
    _script(
        monkeypatch,
        [{"action": "ask_user", "message": "One question before drafting."}],
    )

    result = _say(state, "a zorblat yards platform, like the zorblat kit")

    assert state.product_design.vertical is None
    assert platform_chat_llm.KIT_NOTICE in result["summary"]


def test_the_system_prompt_teaches_kit_honesty_and_connector_choice():
    prompt = " ".join(platform_chat_llm._SYSTEM.split())
    # The model no longer picks a kit: the user's vertical choice decides it.
    assert "kit_match" not in prompt
    assert "Do not pick a kit or a vertical for them" in prompt
    assert "never imply a kit exists when it does not" in prompt
    # The sentence itself is the factory's, so the model cannot skip it.
    for phrase in ("top-notch kit", "take longer", "cost more"):
        assert phrase in platform_chat_llm.KIT_NOTICE
    assert "which outside systems they already use" in prompt
    assert '"connectors"' in prompt and '"missing_connectors"' in prompt
    assert "marked placeholder" in prompt


def test_the_catalog_is_derived_from_manifests_not_names(tmp_path, monkeypatch):
    """Three honest tiers: attachable, in-store-not-cleared, absent."""
    import json

    from app.factory import dual_registry, kit_pack, store_catalog as sc

    reg = tmp_path / "block_registry"
    for bid, tags in (("pager", ["integration"]), ("toolbus", ["mcp"]),
                      ("ledger", ["core"]), ("gdrive", ["integration", "storage"])):
        (reg / bid).mkdir(parents=True)
        (reg / bid / "block.json").write_text(
            json.dumps({"id": bid, "tags": tags, "description": f"{bid} part"}), encoding="utf-8"
        )
    monkeypatch.setattr(
        dual_registry, "load_factory_shelf",
        lambda *a, **k: {b: None for b in ("pager", "toolbus", "ledger")},
    )
    monkeypatch.setattr(
        dual_registry, "load_blocks_registry",
        lambda *a, **k: {b: None for b in ("pager", "toolbus", "ledger", "gdrive")},
    )
    monkeypatch.setattr(kit_pack, "load_shelf_kit_map", lambda *a, **k: {"x": "platform"})

    cat = sc.build_store_catalog(tmp_path)

    assert [e["id"] for e in cat["connectors"]] == ["pager"]
    assert [e["id"] for e in cat["mcp"]] == ["toolbus"]
    assert [e["id"] for e in cat["not_cleared"]] == ["gdrive"]
    assert sc.offerable_connector_ids(cat) == ["pager", "toolbus"]
    text = sc.render_for_chat(cat)
    assert "NOT CLEARED FOR FACTORY BUILDS" in text and "gdrive: gdrive part" in text


# -- the kit notice is the factory's sentence, not the model's -----------------


def _domain_kits(monkeypatch, manifests):
    """The Store's domain kit declarations the kit notice resolves against."""
    from app.factory import store_kits

    monkeypatch.setattr(store_kits, "domain_kits", lambda *a, **k: dict(manifests))


def _kits(monkeypatch, kits):
    from app.factory import store_catalog as sc

    monkeypatch.setattr(
        sc, "store_catalog",
        lambda *a, **k: {"blocks": [], "connectors": [], "mcp": [], "not_cleared": [], "kits": list(kits)},
    )
    # Each kit serves the vertical of the same id and is declared ready.
    _domain_kits(monkeypatch, {k: {"serves_verticals": [k], "build_ready": True} for k in kits})


def test_no_kit_for_the_business_is_said_by_the_factory_not_left_to_the_model(monkeypatch):
    """First live run: the model was told to mention it, and did not."""
    state = _fresh_state()
    _capture_draft(monkeypatch)
    _kits(monkeypatch, ["private_estate_operations"])
    _script(monkeypatch, [{"action": "ask_user", "message": "How many birds?", "kit_match": ""}])

    result = _say(state, "falconry school platform")

    assert result["summary"].startswith(platform_chat_llm.KIT_NOTICE)
    assert result["summary"].endswith("How many birds?")
    for phrase in ("top-notch kit", "take longer", "cost more"):
        assert phrase in result["summary"]


def test_the_notice_is_said_once(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    _kits(monkeypatch, ["private_estate_operations"])
    _script(monkeypatch, [{"action": "ask_user", "message": "q1"}, {"action": "draft_platform", "brief": "b"}])

    first = _say(state, "falconry school")
    second = _say(state, "40 birds")

    assert platform_chat_llm.KIT_NOTICE in first["summary"]
    assert platform_chat_llm.KIT_NOTICE not in second["summary"]


def test_a_chosen_vertical_with_a_ready_kit_suppresses_the_notice(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    _kits(monkeypatch, ["zorblat_yards"])
    state.product_design.vertical = "zorblat_yards"
    _script(monkeypatch, [{"action": "ask_user", "message": "q"}])

    result = _say(state, "a platform to run my yards")

    assert platform_chat_llm.KIT_NOTICE not in result["summary"]
    assert state.product_design.kit_notice_given is False


def test_the_model_cannot_talk_the_notice_away_by_naming_a_kit(monkeypatch):
    """A model that still emits a kit id (even a real one) decides nothing:
    with no vertical chosen, the factory says the no-kit notice."""
    state = _fresh_state()
    _capture_draft(monkeypatch)
    _kits(monkeypatch, ["zorblat_yards"])
    _script(monkeypatch, [{"action": "draft_platform", "brief": "b", "kit_match": "zorblat_yards"}])

    result = _say(state, "falconry school, just build it")

    assert result["summary"].startswith(platform_chat_llm.KIT_NOTICE)


def test_the_generic_base_is_not_offered_as_a_domain_kit(tmp_path, monkeypatch):
    from app.factory import dual_registry, kit_pack, store_catalog as sc

    monkeypatch.setattr(dual_registry, "load_factory_shelf", lambda *a, **k: {})
    monkeypatch.setattr(dual_registry, "load_blocks_registry", lambda *a, **k: {})
    monkeypatch.setattr(
        kit_pack, "load_shelf_kit_map", lambda *a, **k: {"a": "platform", "b": "private_estate_operations"}
    )

    assert sc.build_store_catalog(tmp_path)["kits"] == ["private_estate_operations"]


def test_an_unreadable_shelf_neither_claims_nor_denies_a_kit(monkeypatch):
    state = _fresh_state()
    _capture_draft(monkeypatch)
    from app.factory import store_catalog as sc
    from app.factory import store_kits

    def boom(*a, **k):
        raise RuntimeError("shelf unreadable")

    monkeypatch.setattr(sc, "store_catalog", boom)
    monkeypatch.setattr(store_kits, "domain_kits", boom)
    state.product_design.vertical = "zorblat_yards"
    _script(monkeypatch, [{"action": "ask_user", "message": "q"}])

    result = _say(state, "falconry school")

    assert result["summary"] == "q"
