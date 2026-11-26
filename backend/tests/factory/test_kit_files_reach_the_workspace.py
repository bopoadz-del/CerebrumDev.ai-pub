"""A bound block's Store kit reaches the build: grouped by the Store's own kit
manifests, its kit folder copied into ``kits/<kit>/``.

Every test builds a small Store under ``tmp_path``: no real kit, block or
product is named.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory import dual_registry, kit_pack
from app.factory.kit_pack import kit_map_from_store, kit_memberships_from_store, kits_for_blocks, stock_kits


def _store(root: Path, kits: dict) -> Path:
    """``kits``: kit id -> (status, [block ids], {relative file: text})."""
    for kit_id, (status, blocks, files) in kits.items():
        folder = root / "block_store" / "kits" / kit_id
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(
            json.dumps({"id": kit_id, "status": status, "blocks": blocks}), encoding="utf-8"
        )
        for rel, text in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _store_is_tmp(tmp_path, monkeypatch):
    """The default Store is the test's own folder, so no real Store leaks in."""
    monkeypatch.setattr(dual_registry, "_default_blocks_root", lambda: tmp_path)


@pytest.mark.parametrize(
    "kits, ids, expected",
    [
        (
            {"zorblat_kit": ("available", ["zb_one", "zb_two"], {}), "plinket_kit": ("available", ["pk_one"], {})},
            ["zb_one", "pk_one", "zb_two"],
            {"plinket_kit": ["pk_one"], "zorblat_kit": ["zb_one", "zb_two"]},
        ),
        (
            {"alpha_kit": ("available", ["a1"], {}), "beta_kit": ("available", ["b1", "b2"], {})},
            ["b2", "a1"],
            {"alpha_kit": ["a1"], "beta_kit": ["b2"]},
        ),
    ],
)
def test_blocks_from_two_store_kits_are_grouped_into_both(tmp_path, kits, ids, expected):
    root = _store(tmp_path, kits)
    assert kits_for_blocks(ids, blocks_root=root) == expected


@pytest.mark.parametrize("loose", ["zb_loose", "pk_loose"])
def test_a_block_no_store_kit_lists_takes_the_shelfs_kit(tmp_path, loose):
    """The rule must not fire for a block no Store kit lists: the shelf (or
    the base pack) decides, as before."""
    root = _store(tmp_path, {"zorblat_kit": ("available", ["zb_one"], {})})
    grouped = kits_for_blocks(["zb_one", loose], blocks_root=root)
    assert grouped == {"platform": [loose], "zorblat_kit": ["zb_one"]}


@pytest.mark.parametrize("status", ["in_development", "retired"])
def test_a_kit_the_store_does_not_mark_available_groups_nothing(tmp_path, status):
    root = _store(tmp_path, {"zorblat_kit": (status, ["zb_one"], {})})
    assert kits_for_blocks(["zb_one"], blocks_root=root) == {"platform": ["zb_one"]}


@pytest.mark.parametrize("missing", ["no_store_here", "nor_here"])
def test_with_no_store_the_shelf_decides(tmp_path, missing):
    """An unreadable Store changes nothing: the bundled shelf's own kits."""
    grouped = kits_for_blocks(["estate_registry", "database"], blocks_root=tmp_path / missing)
    assert grouped == {"platform": ["database"], "private_estate_operations": ["estate_registry"]}


def test_a_store_that_raises_leaves_the_shelf_in_charge(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("store unreadable")

    monkeypatch.setattr(kit_pack, "kit_memberships_from_store", boom)
    assert kits_for_blocks(["estate_registry", "zb_one"], blocks_root=tmp_path) == {
        "platform": ["zb_one"],
        "private_estate_operations": ["estate_registry"],
    }


@pytest.mark.parametrize(
    "ids, expected_kit",
    [
        # shared block + two blocks only the second kit holds -> the second kit
        (["shared", "zb_two", "zb_three"], "zorblat_kit"),
        # shared block + two blocks only the first kit holds -> the first kit
        (["shared", "pk_two", "pk_three"], "plinket_kit"),
    ],
)
def test_a_block_in_two_kits_goes_to_the_kit_holding_most_of_the_build(tmp_path, ids, expected_kit):
    root = _store(
        tmp_path,
        {
            "plinket_kit": ("available", ["shared", "pk_two", "pk_three"], {}),
            "zorblat_kit": ("available", ["shared", "zb_two", "zb_three"], {}),
        },
    )
    grouped = kits_for_blocks(ids, blocks_root=root)
    assert "shared" in grouped[expected_kit]
    assert sum("shared" in bids for bids in grouped.values()) == 1


@pytest.mark.parametrize("first, second", [("aa_kit", "bb_kit"), ("kit_m", "kit_z")])
def test_a_tie_goes_to_the_alphabetically_first_kit(tmp_path, first, second):
    root = _store(
        tmp_path,
        {second: ("available", ["shared"], {}), first: ("available", ["shared"], {})},
    )
    assert kits_for_blocks(["shared"], blocks_root=root) == {first: ["shared"]}


@pytest.mark.parametrize("kit", ["zorblat_kit", "plinket_kit"])
def test_an_explicit_shelf_is_honoured_as_is(tmp_path, kit):
    root = _store(tmp_path / "store", {kit: ("available", ["zb_one"], {})})
    shelf = tmp_path / "shelf.json"
    shelf.write_text(json.dumps({"blocks": [{"id": "zb_one", "kit": "shelf_kit"}]}), encoding="utf-8")
    assert kits_for_blocks(["zb_one"], shelf_path=shelf, blocks_root=root) == {"shelf_kit": ["zb_one"]}


@pytest.mark.parametrize(
    "kit, files",
    [
        ("zorblat_kit", {"README.md": "zorblat", "prompts/a.md": "x"}),
        ("plinket_kit", {"data/rates.csv": "a,b", "core/b.py": "y = 1", "c.md": "z"}),
    ],
)
def test_the_kit_folder_is_copied_into_the_build(tmp_path, kit, files):
    """The public stocking path: a Store kit's files land in kits/<kit>/ and
    the lock record counts them -- never a synthesized, empty pack."""
    root = _store(tmp_path / "store", {kit: ("available", ["zb_one"], files)})
    dest = tmp_path / "product"
    records = stock_kits(dest, ["zb_one"], blocks_root=root)
    assert list(records) == [kit]
    assert records[kit]["files_copied"] == len(files) + 1  # + manifest.json
    for rel, text in files.items():
        assert (dest / "kits" / kit / rel).read_text(encoding="utf-8") == text


@pytest.mark.parametrize("order", [("aa_kit", "bb_kit"), ("bb_kit", "aa_kit")])
def test_memberships_list_every_kit_and_the_old_map_keeps_the_last(tmp_path, order):
    root = _store(tmp_path, {k: ("available", ["shared"], {}) for k in order})
    assert kit_memberships_from_store(root) == {"shared": ["aa_kit", "bb_kit"]}
    assert kit_map_from_store(root) == {"shared": "bb_kit"}
