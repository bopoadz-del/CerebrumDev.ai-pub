"""Store selection by relevance: the vertical ranks, it never gates.

A fake Store of synthetic blocks; every test of a rule uses two inputs, and
each rule has a case where it must not fire. No real block, kit, product or
brief is named.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.factory import product_architect, store_kits
from app.factory.product_architect import _blueprint_from_llm_payload
from app.factory.store_relevance import StoreIndex, declared_function, folds, words

# Twelve filler blocks so a word they all share is common (not informative)
# and the INFORMATIVE_SHARE limit (10% of 14 blocks = 1.4) admits only words
# exactly one block declares.
_FILLER: Dict[str, Dict[str, Any]] = {
    f"pad{i:02d}": {
        "name": f"Pad {i}",
        "description": "Generic helper that reads the input and writes the output value",
        "inputs": [{"name": "input"}, {"name": "action", "options": ["value"]}],
    }
    for i in range(12)
}
_STORE: Dict[str, Dict[str, Any]] = {
    **_FILLER,
    "ledger_glider": {
        "name": "Glider ledger",
        "description": "Tracks glider holdback balances",
        "tags": ["holdback", "glider"],
        "inputs": [{"name": "balance_cap"}],
    },
    "quill_roster": {
        "name": "Quill roster",
        "description": "Members and their quill permits",
        "tags": ["quill", "permit"],
        "outputs": [{"name": "roster"}],
    },
}
_DUAL = sorted(_STORE)


@pytest.fixture
def fake_store(monkeypatch):
    monkeypatch.setattr(store_kits, "block_manifests", lambda store_root=None: dict(_STORE))
    return StoreIndex(_STORE)


def _payload(*caps: tuple) -> dict:
    return {
        "capabilities": [
            {"id": cid, "description": desc, "block_ids": list(picks)} for cid, desc, picks in caps
        ]
    }


def _draft(monkeypatch, payload: dict, closure: frozenset = frozenset(), vertical: Any = None):
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: closure)
    return _blueprint_from_llm_payload(payload, "a brief naming no block", vertical, list(_DUAL))


def _bound(bp) -> Dict[str, list]:
    return {cap.id: list(cap.block_ids) for cap in bp.capabilities}


# --- the scorer ---------------------------------------------------------------


@pytest.mark.parametrize(
    "cap_id, text, block, want",
    [
        ("glider_holdback", "keep balances", "ledger_glider", True),
        ("quill_permit", "who holds one", "quill_roster", True),
        ("glider_holdback", "keep balances", "quill_roster", False),
        ("quill_permit", "who holds one", "ledger_glider", False),
    ],
)
def test_a_block_clears_when_its_declaration_shares_the_capabilitys_words(
    fake_store, cap_id, text, block, want
) -> None:
    assert fake_store.clears(block, cap_id, text) is want


@pytest.mark.parametrize(
    "cap_id, text",
    [("value_input", "reads the output value"), ("generic_helper", "writes the input")],
)
def test_words_every_block_declares_are_filler_and_clear_nothing(fake_store, cap_id, text) -> None:
    assert fake_store.rank(cap_id, text) == []
    assert fake_store.best_fit(cap_id, text) is None


@pytest.mark.parametrize(
    "cap_id, text, block",
    [("summary", "glider holdback figures", "ledger_glider"), ("overview", "quill permit list", "quill_roster")],
)
def test_shared_words_only_in_the_prose_do_not_clear(fake_store, cap_id, text, block) -> None:
    """Two rare words shared, but neither is a word of the capability's id:
    the fit is not anchored to what the capability is, so it does not clear."""
    cand = fake_store.candidate(block, cap_id, text)
    assert cand is not None and len(cand.shared) >= 2
    assert cand.clears is False


@pytest.mark.parametrize("block", ["ledger_glider", "quill_roster"])
def test_a_capability_whose_id_holds_the_block_id_clears_on_strong_evidence(fake_store, block) -> None:
    cand = fake_store.candidate(block, f"{block}_view", "")
    assert cand.clears and cand.strong


@pytest.mark.parametrize("block", ["no_such_block", "pad_missing"])
def test_a_block_the_store_holds_no_manifest_for_never_clears(fake_store, block) -> None:
    assert fake_store.candidate(block, block, block) is None
    assert fake_store.clears(block, block, block) is False


@pytest.mark.parametrize("empty", [{}, {"description": ""}])
def test_an_empty_store_or_declaration_ranks_nothing(empty) -> None:
    index = StoreIndex({"lonely": empty} if empty else {})
    assert index.rank("glider_holdback", "balances") == []
    assert index.best_fit("glider_holdback", "balances") is None


@pytest.mark.parametrize("prefer, first", [({"twin_b"}, "twin_b"), ({"twin_a"}, "twin_a")])
def test_the_vertical_breaks_ties_and_never_admits(prefer, first) -> None:
    # Thirty pads: a word two blocks share stays under 10% and is informative.
    twins = {
        "twin_a": {"tags": ["sprocket", "gear"]},
        "twin_b": {"tags": ["sprocket", "gear"]},
        **{f"pad{i:02d}": {"description": "Generic helper"} for i in range(30)},
    }
    index = StoreIndex(twins)
    assert [c.block_id for c in index.rank("sprocket_gear", "", prefer=prefer)][0] == first
    # A preferred block that shares nothing is not ranked at all.
    assert [c.block_id for c in index.rank("sprocket_gear", "", prefer={"pad00"})][:2] == ["twin_a", "twin_b"]


@pytest.mark.parametrize(
    "manifest, want",
    [
        ({"description": "  Tracks   glider holdback  "}, "Tracks glider holdback"),
        ({"name": "Quill roster", "tags": ["quill", "permit"]}, "Quill roster (quill, permit)"),
        ({}, ""),
        ({"description": "x" * 200}, "x" * 107 + "..."),
    ],
)
def test_declared_function_is_one_honest_line(manifest, want) -> None:
    assert declared_function("b", manifest) == want


@pytest.mark.parametrize(
    "text, want",
    [("Glider_Holdbacks and policies", {"glider", "holdbacks", "and", "policies"}), ("a b 42 ox", set())],
)
def test_words_split_ids_and_drop_short_words_and_numbers(text, want) -> None:
    assert words(text) == want


@pytest.mark.parametrize(
    "a, b, want",
    [
        ("holdback", "holdbacks", True),
        ("policy", "policies", True),
        ("quill", "quill", True),
        ("permit", "permission", False),
        ("card", "cart", False),
        ("state", "statement", False),
        # one word is the other run on: four letters in common, one more
        ("tram", "trams", True),
        ("tra", "tras", False),
        ("plon", "plonks", False),
        # both changed at the end: five letters in common, tails 1 and 3
        ("plinky", "plinkies", True),
        ("plinky", "plinkiest", False),
        ("vexilo", "vexila", True),
        ("vexil", "vexim", False),
        ("vexa", "vexo", False),
        ("zorbat", "zorbilt", False),
    ],
)
def test_two_words_fold_by_shape_alone(a, b, want) -> None:
    assert folds(a, b) is want and folds(b, a) is want


@pytest.mark.parametrize(
    "cap_id, text, block",
    [
        ("glider_holdbacks", "keep balance caps", "ledger_glider"),
        ("quill_permits", "member rosters", "quill_roster"),
    ],
)
def test_a_capability_in_another_form_of_the_blocks_words_still_clears(fake_store, cap_id, text, block) -> None:
    cand = fake_store.candidate(block, cap_id, text)
    assert cand is not None and cand.clears and cand.strong


@pytest.mark.parametrize(
    "cap_id, block",
    [("glidepath_permission", "quill_roster"), ("holder_glimmer", "ledger_glider")],
)
def test_a_word_that_only_starts_alike_does_not_fold_into_a_fit(fake_store, cap_id, block) -> None:
    cand = fake_store.candidate(block, cap_id, "")
    assert cand is not None and not cand.clears


# --- the draft rule -----------------------------------------------------------


@pytest.mark.parametrize("vertical", [None, "some_vertical"])
def test_a_relevant_pick_outside_the_kit_is_kept_with_or_without_a_vertical(
    monkeypatch, fake_store, vertical
) -> None:
    """Two capabilities served by blocks that no kit closure holds: both
    picks survive, and the closure being empty or not changes nothing."""
    payload = _payload(
        ("glider_holdback", "keep balances", ["ledger_glider"]),
        ("quill_permit", "who holds one", ["quill_roster"]),
    )
    bp = _draft(monkeypatch, payload, vertical=vertical)
    assert _bound(bp) == {"glider_holdback": ["ledger_glider"], "quill_permit": ["quill_roster"]}


@pytest.mark.parametrize(
    "cap, pick",
    [(("glider_holdback", "keep balances"), "quill_roster"), (("quill_permit", "who holds"), "ledger_glider")],
)
def test_a_pick_the_store_does_not_support_is_dropped(monkeypatch, fake_store, cap, pick) -> None:
    bp = _draft(monkeypatch, _payload((*cap, [pick])))
    assert pick not in _bound(bp)[cap[0]]


@pytest.mark.parametrize("block", ["ledger_glider", "quill_roster"])
def test_a_strong_fit_the_model_left_out_is_bound(monkeypatch, fake_store, block) -> None:
    cap_id = f"{block}_view"
    bp = _draft(monkeypatch, _payload((cap_id, "show it", [])))
    assert _bound(bp) == {cap_id: [block]}
    assert str(getattr(bp.capabilities[0].strategy_hint, "value", "")) == "REUSE"


@pytest.mark.parametrize(
    "cap_id, text",
    [("glider_report", "holdback totals"), ("quill_report", "permit totals")],
)
def test_a_weak_fit_the_model_left_out_is_not_bound(monkeypatch, fake_store, cap_id, text) -> None:
    """One word of the id plus one of the prose clears a model's pick, but is
    not enough to add a block nobody chose: the capability stays GENERATE."""
    bp = _draft(monkeypatch, _payload((cap_id, text, [])))
    assert _bound(bp) == {cap_id: []}
    assert str(getattr(bp.capabilities[0].strategy_hint, "value", "")) == "GENERATE"


@pytest.mark.parametrize(
    "cap_id, text",
    [("glider_report", "holdback totals"), ("quill_report", "permit totals")],
)
def test_the_weak_fit_is_kept_when_the_model_picked_it(monkeypatch, fake_store, cap_id, text) -> None:
    block = "ledger_glider" if cap_id.startswith("glider") else "quill_roster"
    bp = _draft(monkeypatch, _payload((cap_id, text, [block])))
    assert _bound(bp) == {cap_id: [block]}


@pytest.mark.parametrize("cap_id", ["sprocket_audit", "lantern_count"])
def test_a_capability_nothing_serves_stays_generate(monkeypatch, fake_store, cap_id) -> None:
    bp = _draft(monkeypatch, _payload((cap_id, "nothing in the store", [])))
    assert _bound(bp) == {cap_id: []}


@pytest.mark.parametrize("closure", [frozenset({"pad03"}), frozenset({"pad07"})])
def test_a_closure_pick_is_still_kept_without_relevance(monkeypatch, fake_store, closure) -> None:
    """The kit closure's own rule stands: a closure block the model put on a
    capability stays there even when relevance alone would not keep it."""
    (bid,) = closure
    bp = _draft(monkeypatch, _payload(("sprocket_audit", "x", [bid])), closure=closure)
    assert _bound(bp)["sprocket_audit"] == [bid]


@pytest.mark.parametrize("block", ["ledger_glider", "quill_roster"])
def test_the_model_is_shown_each_blocks_declared_function(monkeypatch, fake_store, block) -> None:
    seen: list = []

    def fake_call(messages):
        seen.extend(messages)
        return _payload(("sprocket_audit", "x", []))

    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: set(_DUAL))
    monkeypatch.setattr(product_architect, "_llm_json_call", fake_call)
    monkeypatch.setattr(product_architect, "_certified_ids", lambda: {block})
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: frozenset())
    product_architect._draft_with_llm("brief")
    system = seen[0]["content"]
    want = f"- {block}: {declared_function(block, _STORE[block])}  [certified]"
    assert want in system


_OFF_ID = {
    "nimbus_helper": {"name": "Nimbus helper", "description": "Answers sprocket lattice questions"},
    "corvid_aide": {"name": "Corvid aide", "description": "Explains brindle tamarack readings"},
}


@pytest.mark.parametrize(
    "block, cap_id",
    [("nimbus_helper", "sprocket_lattice_view"), ("corvid_aide", "brindle_tamarack_log")],
)
def test_a_block_matching_only_by_incidental_words_is_not_bound_unasked(monkeypatch, block, cap_id) -> None:
    """Two of the capability's id words in the block's prose, none of them a
    word of the block's own id: a model's pick of it is kept, but it is not
    added when nobody chose it."""
    store = {**_FILLER, **_OFF_ID}
    monkeypatch.setattr(store_kits, "block_manifests", lambda store_root=None: dict(store))
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: frozenset())
    cand = StoreIndex(store).candidate(block, cap_id, "")
    assert cand is not None and cand.clears and not cand.strong
    dual = sorted(store)
    unasked = _blueprint_from_llm_payload(_payload((cap_id, "show it", [])), "a brief", None, dual)
    picked = _blueprint_from_llm_payload(_payload((cap_id, "show it", [block])), "a brief", None, dual)
    assert _bound(unasked) == {cap_id: []}
    assert _bound(picked) == {cap_id: [block]}
