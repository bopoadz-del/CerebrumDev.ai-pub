"""Store selection: the architect LLM picks from the catalogue, code checks facts.

Code never binds a block by its words. A model's pick is kept when the block
is registered and its declared contract covers the inputs and outputs the
capability declares; anything else is GENERATE. A fake Store of synthetic
blocks; every rule is tested on two inputs and has a case where it must not
fire. No real block, kit, product or brief is named.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.factory import product_architect, store_kits
from app.factory.product_architect import _blueprint_from_llm_payload
from app.factory.store_catalog import catalogue_entry, contract_covers, index_line

_STORE: Dict[str, Dict[str, Any]] = {
    "ledger_glider": {
        "name": "Glider ledger",
        "description": "Tracks glider holdback balances",
        "tags": ["holdback"],
        "inputs": [{"name": "balance_cap", "required": True}, {"name": "period"}],
        "outputs": [{"name": "holdback_total"}],
        "when_to_use": "a running balance is kept per glider",
        "when_not": "one-off sums; build them new",
    },
    "quill_roster": {
        "name": "Quill roster",
        "description": "",
        "tags": ["quill", "permit"],
        "inputs": [{"name": "action", "options": ["list", "add"]}],
        "outputs": [{"name": "roster"}, {"name": "count"}],
    },
    # A block whose id is a plain word: a capability named the same must not
    # bind it unless the model picks it AND its contract covers the capability.
    "slate": {
        "name": "Slate",
        "description": "Runs a script on a slate engine",
        "inputs": [{"name": "script"}],
        "outputs": [{"name": "output"}],
    },
    # In the Store with a full contract, but not on the Factory shelf: not
    # registered, so a pick of it is never kept, however well it covers.
    "unshelved_tally": {
        "name": "Tally",
        "description": "Counts tallies",
        "inputs": [{"name": "action"}],
        "outputs": [{"name": "roster"}],
    },
}
_DUAL = sorted(b for b in _STORE if b != "unshelved_tally")


@pytest.fixture
def fake_store(monkeypatch):
    monkeypatch.setattr(store_kits, "block_manifests", lambda store_root=None: dict(_STORE))
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: frozenset())


def _draft(*caps: Dict[str, Any], closure: frozenset = frozenset(), monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: closure)
    payload = {"capabilities": [dict(c) for c in caps]}
    return _blueprint_from_llm_payload(payload, "a brief naming no block", None, list(_DUAL))


def _bound(bp) -> Dict[str, tuple]:
    return {
        c.id: (list(c.block_ids), str(getattr(c.strategy_hint, "value", c.strategy_hint)))
        for c in bp.capabilities
    }


# --- the catalogue entry and its index line ---------------------------------


@pytest.mark.parametrize(
    "bid, want_for, want_in, want_out, want_gaps",
    [
        (
            "ledger_glider",
            "Tracks glider holdback balances",
            ["balance_cap", "period"],
            ["holdback_total"],
            [],
        ),
        (
            "quill_roster",
            "Quill roster (quill, permit)",
            ["action"],
            ["roster", "count"],
            ["when_to_use", "when_not"],
        ),
    ],
)
def test_entry_carries_what_the_manifest_declares(
    bid, want_for, want_in, want_out, want_gaps
) -> None:
    entry = catalogue_entry(bid, _STORE[bid])
    assert entry["id"] == f"tool:{bid}" and entry["kind"] == "tool"
    assert entry["code"] == {"block": bid}
    assert entry["for"] == want_for
    assert [f["name"] for f in entry["inputs"]] == want_in
    assert [f["name"] for f in entry["outputs"]] == want_out
    assert entry["gaps"] == want_gaps
    assert entry["stamps"] == {"verified": None, "tested": None, "approved": None}


@pytest.mark.parametrize("manifest", [{}, None])
def test_entry_of_a_block_that_declares_nothing_is_all_gaps(manifest) -> None:
    entry = catalogue_entry("bare", manifest)
    assert entry["for"] == "" and entry["inputs"] == [] and entry["outputs"] == []
    assert entry["gaps"] == ["for", "inputs", "outputs", "when_to_use", "when_not"]
    line = index_line(entry)
    assert line.startswith("- bare: for: (not declared)")
    assert "use when: (not declared)" in line and "not when: (not declared)" in line
    assert "inputs: (none declared)" in line and "outputs: (none declared)" in line


@pytest.mark.parametrize(
    "bid, parts",
    [
        (
            "ledger_glider",
            [
                "- ledger_glider: for: Tracks glider holdback balances",
                "use when: a running balance is kept per glider",
                "not when: one-off sums; build them new",
                "inputs: balance_cap, period",
                "outputs: holdback_total",
            ],
        ),
        (
            "quill_roster",
            [
                "- quill_roster: for: Quill roster (quill, permit)",
                "use when: (not declared)",
                "inputs: action",
                "outputs: roster, count",
            ],
        ),
    ],
)
def test_index_line_is_one_line_of_declared_facts(bid, parts) -> None:
    line = index_line(catalogue_entry(bid, _STORE[bid]))
    assert "\n" not in line
    for part in parts:
        assert part in line


@pytest.mark.parametrize("count, tail", [(12, ""), (13, " (+1 more)"), (15, " (+3 more)")])
def test_index_line_bounds_the_field_names(count, tail) -> None:
    manifest = {"description": "d", "inputs": [{"name": f"f{i:02d}"} for i in range(count)]}
    line = index_line(catalogue_entry("wide", manifest))
    shown = ", ".join(f"f{i:02d}" for i in range(min(count, 12)))
    assert f"inputs: {shown}{tail} |" in line


# --- the contract check -------------------------------------------------------


@pytest.mark.parametrize(
    "bid, inputs, outputs, want",
    [
        ("ledger_glider", ["balance_cap"], ["holdback_total"], True),
        ("quill_roster", ["action"], ["count"], True),
        ("quill_roster", None, ["Roster"], True),  # names compare without case
        (
            "ledger_glider",
            ["balance_cap", "rate"],
            ["holdback_total"],
            False,
        ),  # one input not declared
        ("quill_roster", ["action"], ["roster", "total"], False),  # one output not declared
        ("ledger_glider", [], [], False),  # declares nothing: never a guess
        ("quill_roster", None, None, False),
        ("ledger_glider", "balance_cap", ["holdback_total"], False),  # not a list
        ("ledger_glider", ["balance_cap", 7], None, False),  # not a name
        ("ledger_glider", [{"name": "period"}], [{"name": "holdback_total"}], True),
        ("ledger_glider", [{"name": " "}], None, False),
    ],
)
def test_contract_covers_only_declared_fields(bid, inputs, outputs, want) -> None:
    assert contract_covers(_STORE[bid], inputs, outputs) is want


@pytest.mark.parametrize("manifest", [{}, None, {"inputs": "x", "outputs": 3}])
def test_a_block_with_no_readable_contract_covers_nothing(manifest) -> None:
    assert contract_covers(manifest, ["x"], None) is False
    assert contract_covers(manifest, None, ["y"]) is False


# --- the public path: _blueprint_from_llm_payload --------------------------------


@pytest.mark.parametrize(
    "cap, want",
    [
        (
            {
                "id": "glider_balances",
                "description": "d",
                "block_ids": ["ledger_glider"],
                "inputs": ["balance_cap"],
                "outputs": ["holdback_total"],
            },
            (["ledger_glider"], "REUSE"),
        ),
        (
            {
                "id": "permit_list",
                "description": "d",
                "block_ids": ["quill_roster"],
                "inputs": ["action"],
                "outputs": ["roster"],
            },
            (["quill_roster"], "REUSE"),
        ),
    ],
)
def test_a_pick_whose_contract_covers_the_capability_is_kept(fake_store, cap, want) -> None:
    assert _bound(_draft(cap))[cap["id"]] == want


@pytest.mark.parametrize(
    "cap",
    [
        # picked, but the capability declares a field the block does not
        {
            "id": "glider_balances",
            "description": "d",
            "block_ids": ["ledger_glider"],
            "inputs": ["crew_size"],
            "outputs": ["holdback_total"],
        },
        # picked, but declares no inputs or outputs at all
        {"id": "permit_list", "description": "d", "block_ids": ["quill_roster"]},
        # picked an id the registry does not hold
        {
            "id": "permit_list",
            "description": "d",
            "block_ids": ["not_a_block"],
            "inputs": ["action"],
            "outputs": ["roster"],
        },
        # covered by the Store manifest, but the block is not registered
        {
            "id": "permit_list",
            "description": "d",
            "block_ids": ["unshelved_tally"],
            "inputs": ["action"],
            "outputs": ["roster"],
        },
        # block_ids is not a list
        {
            "id": "permit_list",
            "description": "d",
            "block_ids": "quill_roster",
            "inputs": ["action"],
            "outputs": ["roster"],
        },
    ],
)
def test_a_pick_the_facts_do_not_back_is_generate(fake_store, cap) -> None:
    assert _bound(_draft(cap))[cap["id"]] == ([], "GENERATE")


@pytest.mark.parametrize(
    "cap",
    [
        # named exactly for a block, nothing picked
        {"id": "slate", "description": "the contract slate each line refers to", "block_ids": []},
        # named for a block plus a shared word, nothing picked
        {
            "id": "quill_roster_view",
            "description": "list quill permit members",
            "block_ids": [],
            "inputs": ["action"],
            "outputs": ["roster"],
        },
    ],
)
def test_code_never_binds_a_block_the_model_did_not_pick(fake_store, cap) -> None:
    assert _bound(_draft(cap))[cap["id"]] == ([], "GENERATE")


@pytest.mark.parametrize(
    "closure, cap",
    [
        (
            frozenset({"quill_roster"}),
            {
                "id": "glider_balances",
                "description": "d",
                "block_ids": ["ledger_glider"],
                "inputs": ["period"],
                "outputs": ["holdback_total"],
            },
        ),
        (
            frozenset({"ledger_glider"}),
            {
                "id": "run_script",
                "description": "d",
                "block_ids": ["slate"],
                "inputs": ["script"],
                "outputs": ["output"],
            },
        ),
    ],
)
def test_the_vertical_never_gates_a_covered_pick(fake_store, monkeypatch, closure, cap) -> None:
    bound = _bound(_draft(cap, closure=closure, monkeypatch=monkeypatch))
    assert bound[cap["id"]] == (cap["block_ids"], "REUSE")


@pytest.mark.parametrize(
    "closure, cap",
    [
        (
            frozenset({"quill_roster"}),
            {
                "id": "permit_list",
                "description": "d",
                "block_ids": ["quill_roster"],
                "inputs": ["crew"],
                "outputs": ["roster"],
            },
        ),
        (frozenset({"slate"}), {"id": "run_script", "description": "d", "block_ids": ["slate"]}),
    ],
)
def test_being_in_the_vertical_kit_does_not_excuse_an_uncovered_pick(
    fake_store, monkeypatch, closure, cap
) -> None:
    bound = _bound(_draft(cap, closure=closure, monkeypatch=monkeypatch))
    assert bound[cap["id"]] == ([], "GENERATE")


@pytest.mark.parametrize(
    "picks, want",
    [
        (["ledger_glider", "quill_roster"], ["ledger_glider"]),
        (["quill_roster", "ledger_glider", "ledger_glider"], ["ledger_glider"]),
    ],
)
def test_each_pick_is_checked_on_its_own(fake_store, picks, want) -> None:
    cap = {
        "id": "glider_balances",
        "description": "d",
        "block_ids": picks,
        "inputs": ["period"],
        "outputs": ["holdback_total"],
    }
    assert _bound(_draft(cap))["glider_balances"] == (want, "REUSE")


@pytest.mark.parametrize(
    "item, want",
    [
        ({"block_ids": ["a", 3, None, "b"]}, ["a", "b"]),
        ({"block_ids": "a"}, []),
        ({}, []),
    ],
)
def test_raw_picks_keeps_only_listed_strings(item, want) -> None:
    assert product_architect.raw_picks(item) == want


# --- the prompt the architect reads ------------------------------------------------


@pytest.mark.parametrize("proven", [set(), {"slate"}])
def test_the_prompt_offers_every_block_as_a_catalogue_line(monkeypatch, fake_store, proven) -> None:
    seen: Dict[str, Any] = {}

    def fake_call(messages):
        seen["system"] = messages[0]["content"]
        return {"capabilities": [{"id": "x", "description": "d", "block_ids": []}]}

    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: set(_DUAL))
    monkeypatch.setattr(product_architect, "_certified_ids", lambda: set(proven))
    monkeypatch.setattr(product_architect, "_llm_json_call", fake_call)
    product_architect._draft_with_llm("a brief", vertical_hint="some_vertical")
    system = seen["system"]
    for bid in _DUAL:
        line = index_line(catalogue_entry(bid, _STORE[bid]))
        assert line in system
        assert (line + "  [certified]" in system) is (bid in proven)
    assert "unshelved_tally" not in system
    assert '"inputs": [' in system and '"outputs": [' in system
