"""A domain kit reaches the workspace because selection drew it.

The CLONER grouped vendored blocks by the Factory's bundled shelf, which
names one domain kit, so a Store domain kit never reached a build
("synthesized from the Factory dual-register shelf", files_copied 0). Now a
bound block draws a Store kit when the kit lists it AND either the kit's own
declaration clears the relevance floor for the capability the block serves,
or the block's own declaration names the kit by a word of the kit's id. A
kit that merely lists a generic block it was composed with is not drawn.

Every word, block and kit here is made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.workspace import RoleWorkspace
from app.factory.kit_pack import (
    capability_bindings,
    drawn_kits,
    kits_for_blocks,
    stock_kits,
    stock_kits_via_workspace,
)
from app.factory.store_relevance import StoreIndex, kit_index

# Thirty plain blocks give the Store a prose vocabulary, so "with", "from",
# "plain" and "base" are filler the way they are in the real Store.
_FILLER = {
    f"filler_{i}": {"description": f"plain helper with from the base number {i}"}
    for i in range(30)
}
_BLOCKS = {
    **_FILLER,
    "zorvex_ledger": {"description": "zorvex ledger posting", "tags": ["zorvex"]},
    "grid_panel": {"description": "panel of tiles"},
    "quillon_registry": {"description": "quillon registry for lots", "tags": ["quillon"]},
    "pagereader": {"description": "page reader"},
    "dormant_tool": {"description": "dormant tool"},
}
_KITS = {
    "zorvex_ops": {
        "status": "available",
        "name": "Zorvex Ops",
        "description": "Zorvex operations ledger reconciliation",
        "blocks": ["zorvex_ledger", "pagereader"],
    },
    "marrow_estate": {
        "status": "available",
        "name": "Marrow Estate",
        "description": "Marrow estate household staff rota with an owner panel from the steward",
        "blocks": ["grid_panel", "quillon_registry", "filler_0"],
    },
    "quillon_land": {
        "status": "available",
        "name": "Quillon Land",
        "description": "Quillon land parcels survey",
        "blocks": ["quillon_registry", "pagereader"],
    },
    "dormant": {
        "status": "in_development",
        "description": "dormant tool ledger",
        "blocks": ["dormant_tool", "zorvex_ledger"],
    },
    # Template suites, like the Store's domain suites: one shared sentence,
    # so its words are filler across kits.
    **{
        f"suite_{i}": {
            "status": "available",
            "description": f"Suite {i}: typed document analysis, entity extraction and scoring",
            "blocks": ["pagereader"],
        }
        for i in range(8)
    },
}


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
            NS(id=cid, description=text, requirements=[], block_ids=list(blocks))
            for cid, text, blocks in caps
        ]
    )


@pytest.mark.parametrize(
    "cap, kit",
    [
        (("ledger_reconciliation", "reconcile zorvex ledger postings", ["zorvex_ledger"]), "zorvex_ops"),
        (("parcels_survey", "survey quillon land parcels", ["quillon_registry"]), "quillon_land"),
    ],
)
def test_a_kit_whose_declaration_fits_the_capability_is_drawn(store, cap, kit):
    drawn = drawn_kits([cap], store)

    assert drawn[cap[2][0]] == {"kit": kit, "for": [cap[0]]}


@pytest.mark.parametrize(
    "cap",
    [
        ("certificate_overview", "overview of certificates", ["grid_panel"]),
        ("payment_flow", "payment steps", ["filler_0"]),
    ],
)
def test_a_generic_block_does_not_draw_the_kit_that_merely_lists_it(store, cap):
    """The kit lists the block, but neither does the kit fit the capability
    nor does the block name the kit: it goes to the base pack."""
    assert drawn_kits([cap], store) == {}


@pytest.mark.parametrize(
    "cap, kit",
    [
        (("intake", "take in records", ["quillon_registry"]), "quillon_land"),
        (("posting", "post entries", ["zorvex_ledger"]), "zorvex_ops"),
    ],
)
def test_a_block_that_names_its_kit_draws_it(store, cap, kit):
    """The kit's text does not fit the capability, but the block declares
    the kit's own id word; a kit that is not available is never drawn."""
    assert drawn_kits([cap], store)[cap[2][0]]["kit"] == kit


@pytest.mark.parametrize(
    "cap",
    [
        ("staff_with", "staff with plain", ["grid_panel"]),
        ("rota_from", "rota from plain", ["grid_panel"]),
    ],
)
def test_a_word_common_in_the_store_prose_is_filler_in_the_kit_index(store, cap):
    """With only one shared word that is not filler, the kit does not clear."""
    assert drawn_kits([cap], store) == {}
    kits = {k: dict(v, id=k) for k, v in _KITS.items() if v["status"] == "available"}
    bare = kit_index(kits)
    assert bare.clears("marrow_estate", cap[0], cap[1]), "the filler word is what clears without the rule"


@pytest.mark.parametrize(
    "cap, kit",
    [
        (("estate_lots", "marrow estate lots", ["quillon_registry"]), "marrow_estate"),
        (("lots_survey", "survey of land lots", ["quillon_registry"]), "quillon_land"),
        # The named kit shares more words (a higher score) but none of them
        # is a word of the capability's id, so it does not clear; the kit
        # that clears still wins.
        (("rota_tally", "household rota; quillon land parcels survey", ["quillon_registry"]), "marrow_estate"),
    ],
)
def test_a_kit_that_fits_the_capability_beats_one_the_block_names(store, cap, kit):
    assert drawn_kits([cap], store)["quillon_registry"]["kit"] == kit


@pytest.mark.parametrize(
    "cap",
    [
        ("dormant_tool", "dormant tool", ["dormant_tool"]),
        ("tool_ledger", "dormant tool ledger", ["dormant_tool"]),
    ],
)
def test_a_kit_not_marked_available_is_never_drawn(store, cap):
    assert drawn_kits([cap], store) == {}


@pytest.mark.parametrize("root", [None, "missing"])
def test_no_store_kits_draws_nothing(tmp_path, monkeypatch, root):
    from app.factory import kit_pack

    monkeypatch.setattr(kit_pack, "_store_root", lambda _r: None if root is None else tmp_path / root)
    assert drawn_kits([("ledger_reconciliation", "zorvex ledger", ["zorvex_ledger"])]) == {}


@pytest.mark.parametrize(
    "caps, expected",
    [
        (
            [NS(id="a_cap", description="one", requirements=["two"], block_ids=["x"])],
            [("a_cap", "one two", ["x"])],
        ),
        (
            [
                NS(id="", description="d", requirements=[], block_ids=["x"]),
                NS(id="no_blocks", description="d", requirements=[], block_ids=[]),
                NS(capability_id="planned", description="p", block_ids=["y", " "]),
            ],
            [("planned", "p", ["y"])],
        ),
    ],
)
def test_capability_bindings_reads_id_text_and_blocks(caps, expected):
    assert capability_bindings(NS(capabilities=caps)) == expected


@pytest.mark.parametrize(
    "cap, kit, block",
    [
        (("ledger_reconciliation", "reconcile zorvex ledger postings", ["zorvex_ledger"]), "zorvex_ops", "zorvex_ledger"),
        (("parcels_survey", "survey quillon land parcels", ["quillon_registry"]), "quillon_land", "quillon_registry"),
    ],
)
def test_stock_kits_copies_the_drawn_kit_and_records_why(store, shelf, tmp_path, cap, kit, block):
    out = tmp_path / "product"
    records = stock_kits(out, [block, "grid_panel"], blocks_root=store, shelf_path=shelf, blueprint=_bp(cap))

    assert (out / "kits" / kit / "bundle" / "readme.txt").is_file()
    assert records[kit]["files_copied"] == 2
    assert records[kit]["blocks"] == [block]
    assert records[kit]["drawn_for"] == [cap[0]]
    assert records["platform"]["blocks"] == ["grid_panel"]
    assert "drawn_for" not in records["platform"]


@pytest.mark.parametrize("block", ["zorvex_ledger", "quillon_registry"])
def test_without_a_blueprint_the_grouping_is_the_shelfs(store, shelf, tmp_path, block):
    records = stock_kits(tmp_path / "product", [block], blocks_root=store, shelf_path=shelf)

    assert list(records) == ["platform"]
    assert "drawn_for" not in records["platform"]
    assert kits_for_blocks([block], shelf_path=shelf) == {"platform": [block]}


@pytest.mark.parametrize(
    "cap, kit, block",
    [
        (("ledger_reconciliation", "reconcile zorvex ledger postings", ["zorvex_ledger"]), "zorvex_ops", "zorvex_ledger"),
        (("intake", "take in records", ["quillon_registry"]), "quillon_land", "quillon_registry"),
    ],
)
def test_the_cloner_path_stocks_the_drawn_kit(store, shelf, tmp_path, cap, kit, block):
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    records = stock_kits_via_workspace(ws, [block], blocks_root=store, shelf_path=shelf, blueprint=_bp(cap))

    assert (ws.destination / "kits" / kit / "bundle" / "readme.txt").is_file()
    assert records[kit]["files_copied"] == 2
    assert records[kit]["drawn_for"] == [cap[0]]


@pytest.mark.parametrize(
    "cap, kit",
    [
        (("ledger_reconciliation", "reconcile zorvex ledger postings", ["zorvex_ledger"]), "zorvex_ops"),
        (("parcels_survey", "survey quillon land parcels", ["quillon_registry"]), "quillon_land"),
    ],
)
def test_the_writer_brief_names_the_kit_the_workspace_holds(store, monkeypatch, cap, kit):
    from app.factory import kit_pack
    from app.factory.build import brief_compiler

    monkeypatch.setattr(kit_pack, "load_shelf_kit_map", lambda *a, **k: {})
    with_bp = brief_compiler._load_kit_manifests(cap[2], blocks_root=store, blueprint=_bp(cap))
    without = brief_compiler._load_kit_manifests(cap[2], blocks_root=store)

    assert list(with_bp) == [kit]
    assert list(without) == ["platform"]


@pytest.mark.parametrize("n, common", [(30, {"plain", "with", "from", "base"}), (5, set())])
def test_common_words_are_the_store_prose_filler(n, common):
    manifests = {f"b{i}": {"description": "plain with from base"} for i in range(n)}
    manifests.update({f"r{i}": {"description": f"rare{i}word"} for i in range(10)})

    found = StoreIndex(manifests, share=0.5 if n == 5 else 0.1).common_words()

    assert found == common
