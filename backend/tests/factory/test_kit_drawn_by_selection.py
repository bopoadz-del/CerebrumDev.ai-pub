"""A domain kit reaches the workspace because the architect picked it.

The CLONER grouped vendored blocks by the Factory's bundled shelf, which
names one domain kit, so a Store domain kit never reached a build
("synthesized from the Factory dual-register shelf", files_copied 0). Now
the architect is shown one index line per available Store kit and picks a
capability's kits itself; code checks facts only. A block draws a kit when a
capability that binds the block picked the kit, the Store marks the kit
available, and the kit's manifest lists the block. A kit that merely lists a
generic block it was composed with is never drawn: only a pick draws it.

Every word, block and kit here is made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.factory import kit_pack, product_architect, store_kits
from app.factory.build.authority import BuildRole
from app.factory.build.workspace import RoleWorkspace
from app.factory.kit_pack import (
    capability_picks,
    drawn_kits,
    kits_for_blocks,
    stock_kits,
    stock_kits_via_workspace,
)
from app.factory.product_architect import _blueprint_from_llm_payload
from app.factory.store_catalog import kept_kit_picks, kit_index_line

_BLOCKS = {
    "zorvex_ledger": {"description": "zorvex ledger posting"},
    "grid_panel": {"description": "panel of tiles"},
    "quillon_registry": {"description": "quillon registry for lots"},
    "pagereader": {"description": "page reader"},
    "dormant_tool": {"description": "dormant tool"},
}
_KITS = {
    "zorvex_ops": {
        "status": "available",
        "description": "Zorvex operations ledger reconciliation",
        "when_to_use": "a ledger is reconciled per period",
        "blocks": ["zorvex_ledger", "pagereader"],
    },
    # Lists generic blocks it was composed with, like a real domain kit.
    "marrow_estate": {
        "status": "available",
        "description": "Marrow estate household staff rota",
        "blocks": ["grid_panel", "quillon_registry", "pagereader"],
    },
    "quillon_land": {
        "status": "available",
        "description": "Quillon land parcels survey",
        "blocks": ["quillon_registry", "pagereader"],
    },
    "dormant": {
        "status": "in_development",
        "description": "dormant tool ledger",
        "blocks": ["dormant_tool", "zorvex_ledger"],
    },
}
_AVAILABLE = {k: dict(v, id=k) for k, v in _KITS.items() if v["status"] == "available"}


def _store(root: Path) -> Path:
    for bid, manifest in _BLOCKS.items():
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid, **manifest}), encoding="utf-8")
    for kit_id, manifest in _KITS.items():
        d = root / "block_store" / "kits" / kit_id
        (d / "bundle").mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"id": kit_id, **manifest}), encoding="utf-8")
        (d / "bundle" / "readme.txt").write_text(f"{kit_id} bundle\n", encoding="utf-8")
    return root


@pytest.fixture()
def store(tmp_path):
    return _store(tmp_path / "store")


@pytest.fixture()
def shelf(tmp_path):
    path = tmp_path / "shelf.json"
    path.write_text(json.dumps({"blocks": []}), encoding="utf-8")
    return path


def _bp(*caps):
    return NS(
        capabilities=[
            NS(id=cid, block_ids=list(blocks), kit_ids=list(kits)) for cid, blocks, kits in caps
        ]
    )


# --- drawn_kits: a pick, checked on facts -------------------------------------


@pytest.mark.parametrize(
    "pick, block, kit",
    [
        (("ledger_reconciliation", ["zorvex_ledger"], ["zorvex_ops"]), "zorvex_ledger", "zorvex_ops"),
        (("parcels_survey", ["quillon_registry"], ["quillon_land"]), "quillon_registry", "quillon_land"),
    ],
)
def test_a_picked_kit_that_lists_the_block_is_drawn(store, pick, block, kit):
    assert drawn_kits([pick], store) == {block: {"kit": kit, "for": [pick[0]]}}


@pytest.mark.parametrize(
    "pick",
    [
        ("certificate_overview", ["grid_panel"], []),
        ("parcels_survey", ["quillon_registry"], []),
    ],
)
def test_a_kit_that_lists_the_block_but_was_not_picked_is_not_drawn(store, pick):
    """Two kits list quillon_registry and one lists grid_panel; with no pick
    none of them is drawn."""
    assert drawn_kits([pick], store) == {}


@pytest.mark.parametrize(
    "pick",
    [
        ("certificate_overview", ["grid_panel"], ["zorvex_ops"]),
        ("ledger_reconciliation", ["zorvex_ledger"], ["quillon_land"]),
    ],
)
def test_a_picked_kit_that_does_not_list_the_block_is_not_drawn(store, pick):
    assert drawn_kits([pick], store) == {}


@pytest.mark.parametrize(
    "pick",
    [
        ("dormant_tool", ["dormant_tool"], ["dormant"]),
        ("tool_ledger", ["zorvex_ledger"], ["dormant", "no_such_kit"]),
    ],
)
def test_a_kit_not_marked_available_or_unknown_is_never_drawn(store, pick):
    assert drawn_kits([pick], store) == {}


@pytest.mark.parametrize(
    "picks, want",
    [
        # Two capabilities bind the same block and pick different kits: the
        # first capability's pick wins, in blueprint order.
        (
            [
                ("lots", ["quillon_registry"], ["quillon_land"]),
                ("rota", ["quillon_registry"], ["marrow_estate"]),
            ],
            {"quillon_registry": {"kit": "quillon_land", "for": ["lots"]}},
        ),
        (
            [
                ("rota", ["quillon_registry"], ["marrow_estate"]),
                ("lots", ["quillon_registry"], ["quillon_land"]),
            ],
            {"quillon_registry": {"kit": "marrow_estate", "for": ["rota"]}},
        ),
    ],
)
def test_the_first_capabilitys_pick_wins_a_shared_block(store, picks, want):
    assert drawn_kits(picks, store) == want


@pytest.mark.parametrize(
    "picks, want",
    [
        # A later capability picking the same kit is recorded as drawing it too.
        (
            [
                ("survey", ["pagereader"], ["quillon_land"]),
                ("intake", ["pagereader", "quillon_registry"], ["quillon_land"]),
            ],
            {
                "pagereader": {"kit": "quillon_land", "for": ["intake", "survey"]},
                "quillon_registry": {"kit": "quillon_land", "for": ["intake"]},
            },
        ),
        # A first pick that does not list the block gives way to the next pick.
        (
            [("posting", ["zorvex_ledger", "grid_panel"], ["marrow_estate", "zorvex_ops"])],
            {
                "grid_panel": {"kit": "marrow_estate", "for": ["posting"]},
                "zorvex_ledger": {"kit": "zorvex_ops", "for": ["posting"]},
            },
        ),
    ],
)
def test_every_capability_that_drew_a_kit_is_recorded(store, picks, want):
    assert drawn_kits(picks, store) == want


@pytest.mark.parametrize("root", [None, "missing"])
def test_no_store_kits_draws_nothing(tmp_path, monkeypatch, root):
    monkeypatch.setattr(kit_pack, "_store_root", lambda _r: None if root is None else tmp_path / root)
    assert drawn_kits([("ledger_reconciliation", ["zorvex_ledger"], ["zorvex_ops"])]) == {}


@pytest.mark.parametrize(
    "caps, expected",
    [
        (
            [NS(id="a_cap", block_ids=["x"], kit_ids=["k"])],
            [("a_cap", ["x"], ["k"])],
        ),
        (
            [
                NS(id="", block_ids=["x"], kit_ids=["k"]),
                NS(id="no_blocks", block_ids=[], kit_ids=["k"]),
                NS(id="no_kits", block_ids=["x"], kit_ids=[]),
                NS(id="kitless", block_ids=["x"]),
                NS(capability_id="planned", block_ids=["y", " "], kit_ids=["k2", ""]),
            ],
            [("planned", ["y"], ["k2"])],
        ),
    ],
)
def test_capability_picks_reads_id_blocks_and_kits(caps, expected):
    assert capability_picks(NS(capabilities=caps)) == expected


# --- the three users: offline generator, CLONER, writer brief -----------------


@pytest.mark.parametrize(
    "pick, kit, block",
    [
        (("ledger_reconciliation", ["zorvex_ledger"], ["zorvex_ops"]), "zorvex_ops", "zorvex_ledger"),
        (("parcels_survey", ["quillon_registry"], ["quillon_land"]), "quillon_land", "quillon_registry"),
    ],
)
def test_stock_kits_copies_the_drawn_kit_and_records_why(store, shelf, tmp_path, pick, kit, block):
    out = tmp_path / "product"
    records = stock_kits(out, [block, "grid_panel"], blocks_root=store, shelf_path=shelf, blueprint=_bp(pick))

    assert (out / "kits" / kit / "bundle" / "readme.txt").is_file()
    assert records[kit]["files_copied"] == 2
    assert records[kit]["blocks"] == [block]
    assert records[kit]["drawn_for"] == [pick[0]]
    assert records["platform"]["blocks"] == ["grid_panel"]
    assert "drawn_for" not in records["platform"]


@pytest.mark.parametrize("block", ["zorvex_ledger", "quillon_registry"])
def test_without_a_blueprint_the_grouping_is_the_shelfs(store, shelf, tmp_path, block):
    records = stock_kits(tmp_path / "product", [block], blocks_root=store, shelf_path=shelf)

    assert list(records) == ["platform"]
    assert "drawn_for" not in records["platform"]
    assert kits_for_blocks([block], shelf_path=shelf) == {"platform": [block]}


@pytest.mark.parametrize(
    "pick, kit, block",
    [
        (("ledger_reconciliation", ["zorvex_ledger"], ["zorvex_ops"]), "zorvex_ops", "zorvex_ledger"),
        (("intake", ["quillon_registry"], ["marrow_estate"]), "marrow_estate", "quillon_registry"),
    ],
)
def test_the_cloner_path_stocks_the_drawn_kit(store, shelf, tmp_path, pick, kit, block):
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    records = stock_kits_via_workspace(ws, [block], blocks_root=store, shelf_path=shelf, blueprint=_bp(pick))

    assert (ws.destination / "kits" / kit / "bundle" / "readme.txt").is_file()
    assert records[kit]["files_copied"] == 2
    assert records[kit]["drawn_for"] == [pick[0]]


@pytest.mark.parametrize(
    "pick, kit",
    [
        (("ledger_reconciliation", ["zorvex_ledger"], ["zorvex_ops"]), "zorvex_ops"),
        (("parcels_survey", ["quillon_registry"], ["quillon_land"]), "quillon_land"),
    ],
)
def test_the_writer_brief_names_the_kit_the_workspace_holds(store, monkeypatch, pick, kit):
    from app.factory.build import brief_compiler

    monkeypatch.setattr(kit_pack, "load_shelf_kit_map", lambda *a, **k: {})
    with_bp = brief_compiler._load_kit_manifests(pick[1], blocks_root=store, blueprint=_bp(pick))
    without = brief_compiler._load_kit_manifests(pick[1], blocks_root=store)

    assert list(with_bp) == [kit]
    assert list(without) == ["platform"]


# --- the kit index line and the architect's fact check ------------------------


@pytest.mark.parametrize(
    "kit_id, parts",
    [
        (
            "zorvex_ops",
            [
                "- zorvex_ops: for: Zorvex operations ledger reconciliation",
                "use when: a ledger is reconciled per period",
                "not when: (not declared)",
                "blocks: zorvex_ledger, pagereader",
            ],
        ),
        ("bare", ["- bare: for: (not declared)", "use when: (not declared)", "blocks: (none listed)"]),
    ],
)
def test_kit_index_line_is_one_line_of_the_kits_own_declaration(kit_id, parts):
    manifest = _KITS.get(kit_id, {"blocks": ["", 3]})
    line = kit_index_line(kit_id, manifest)

    assert "\n" not in line
    for part in parts:
        assert part in line


@pytest.mark.parametrize(
    "picks, blocks, want",
    [
        (["zorvex_ops", "quillon_land"], ["zorvex_ledger"], ["zorvex_ops"]),
        (["quillon_land", "marrow_estate", "quillon_land"], ["quillon_registry"], ["quillon_land", "marrow_estate"]),
        (["dormant", "no_such_kit", 7, None], ["zorvex_ledger", "dormant_tool"], []),
        (["zorvex_ops"], [], []),
        ("zorvex_ops", ["zorvex_ledger"], []),
        (None, ["zorvex_ledger"], []),
    ],
)
def test_kept_kit_picks_keeps_only_available_kits_listing_a_bound_block(picks, blocks, want):
    assert kept_kit_picks(picks, blocks, _AVAILABLE) == want


@pytest.fixture
def fake_architect_store(monkeypatch):
    manifests = {b: dict(m, outputs=[{"name": "out"}]) for b, m in _BLOCKS.items()}
    monkeypatch.setattr(store_kits, "block_manifests", lambda store_root=None: manifests)
    monkeypatch.setattr(store_kits, "kit_closure", lambda vertical, *a, **k: frozenset())
    monkeypatch.setattr(kit_pack, "available_kit_manifests", lambda blocks_root=None: dict(_AVAILABLE))


@pytest.mark.parametrize(
    "cap, want",
    [
        ({"id": "posting", "block_ids": ["zorvex_ledger"], "outputs": ["out"], "kit_ids": ["zorvex_ops", "quillon_land"]}, ["zorvex_ops"]),
        ({"id": "lots", "block_ids": ["quillon_registry"], "outputs": ["out"], "kit_ids": ["marrow_estate"]}, ["marrow_estate"]),
        # The block pick fails its contract check, so no block is kept and no
        # kit pick can be backed by one.
        ({"id": "posting", "block_ids": ["zorvex_ledger"], "outputs": ["nope"], "kit_ids": ["zorvex_ops"]}, []),
        ({"id": "posting", "block_ids": ["zorvex_ledger"], "outputs": ["out"], "kit_ids": ["dormant"]}, []),
        ({"id": "posting", "block_ids": ["zorvex_ledger"], "outputs": ["out"]}, []),
    ],
)
def test_the_architect_keeps_a_kit_pick_only_on_facts(fake_architect_store, cap, want):
    bp = _blueprint_from_llm_payload(
        {"capabilities": [dict(cap, description="d")]}, "a brief", None, sorted(_BLOCKS)
    )

    assert bp.capabilities[0].kit_ids == want


@pytest.mark.parametrize("kits", [{}, {"zorvex_ops": _AVAILABLE["zorvex_ops"]}])
def test_the_prompt_offers_every_available_kit_as_an_index_line(monkeypatch, fake_architect_store, kits):
    seen = {}

    def fake_call(messages):
        seen["system"] = messages[0]["content"]
        return {"capabilities": [{"id": "x", "description": "d", "block_ids": []}]}

    monkeypatch.setattr(kit_pack, "available_kit_manifests", lambda blocks_root=None: dict(kits))
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: set(_BLOCKS))
    monkeypatch.setattr(product_architect, "_certified_ids", lambda: set())
    monkeypatch.setattr(product_architect, "_llm_json_call", fake_call)
    product_architect._draft_with_llm("a brief", vertical_hint="some_vertical")

    kit_part = seen["system"].split("\nKITS:\n", 1)[1]
    for kit_id, manifest in kits.items():
        assert kit_index_line(kit_id, manifest) in kit_part
    assert ("- (none available)" in kit_part) is (not kits)
    assert '"kit_ids": [' in seen["system"]


# --- edges: what a pick may not do -------------------------------------------


@pytest.mark.parametrize(
    "kit, block, name, inputs",
    [
        ("zorvex_ops", "zorvex_ledger", "Zorvex Ops Pack", {"zorvex_ledger": ["period"]}),
        ("quillon_land", "quillon_registry", "Quillon Parcels", {"quillon_registry": ["parcel_ref"]}),
    ],
)
def test_the_brief_kit_view_carries_the_stores_own_manifest(store, monkeypatch, kit, block, name, inputs):
    """``_load_kit_manifests`` reads the Store's manifest, so the brief shows
    the kit's declared name and contract, not a synthesized stand-in."""
    from app.factory.build import brief_compiler

    path = store / "block_store" / "kits" / kit / "manifest.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(dict(body, name=name, inputs=inputs)), encoding="utf-8")
    monkeypatch.setattr(kit_pack, "load_shelf_kit_map", lambda *a, **k: {})

    pick = ("cap_" + block, [block], [kit])
    view = brief_compiler._load_kit_manifests([block], blocks_root=store, blueprint=_bp(pick))[kit]

    assert view["name"] == name
    assert view["inputs"] == inputs


@pytest.mark.parametrize(
    "records, drawn",
    [
        # The drawn kit was never stocked: nothing to record on, no error.
        ({"platform": {"blocks": ["grid_panel"]}}, {"zorvex_ledger": {"kit": "zorvex_ops", "for": ["c1"]}}),
        # The kit was stocked but without this block: it was not drawn for it.
        ({"quillon_land": {"blocks": ["pagereader"]}}, {"quillon_registry": {"kit": "quillon_land", "for": ["c2"]}}),
    ],
)
def test_with_drawn_for_records_nothing_a_stocked_kit_does_not_hold(records, drawn):
    before = json.loads(json.dumps(records))
    assert kit_pack._with_drawn_for(records, drawn) == before


@pytest.mark.parametrize("blocks_root", ["store-a", "store-b"])
def test_copy_referenced_kits_does_not_consult_the_store_for_a_product_without_blocks(
    tmp_path, monkeypatch, blocks_root
):
    """A product with no vendored blocks stocks no kit and never reads the
    Store for one (``_copy_referenced_kits``)."""
    from app.factory.generator import ProductGenerator

    def _must_not_run(*_a, **_k):
        raise AssertionError("stock_kits ran for a product with no blocks")

    monkeypatch.setattr(kit_pack, "stock_kits", _must_not_run)
    gen = object.__new__(ProductGenerator)
    gen.plan = NS(dual_registered_blocks=[])
    gen.blocks_root = tmp_path / blocks_root
    gen.blueprint = _bp(("c", [], ["zorvex_ops"]))
    out = tmp_path / "product"

    gen._copy_referenced_kits(out)

    assert not (out / "kits").exists()


@pytest.mark.parametrize("block", ["zorvex_ledger", "quillon_registry"])
def test_copy_referenced_kits_stocks_the_kits_of_a_product_with_blocks(tmp_path, monkeypatch, block):
    from app.factory.generator import ProductGenerator

    seen = {}
    monkeypatch.setattr(kit_pack, "stock_kits", lambda out, ids, **k: seen.update(ids=list(ids)))
    gen = object.__new__(ProductGenerator)
    gen.plan = NS(dual_registered_blocks=[block])
    gen.blocks_root = tmp_path
    gen.blueprint = _bp(("c", [block], []))

    gen._copy_referenced_kits(tmp_path / "product")

    assert seen == {"ids": [block]}
