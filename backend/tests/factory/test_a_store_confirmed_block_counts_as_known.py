"""A block the Store's exact-id lookup confirms counts as known to the
brief, even when the local shelf has not caught up (``compile_brief``).

Every capability and block id here is made up.
"""

from __future__ import annotations

import pytest

from app.factory.build.brief_compiler import compile_brief
from app.factory.build.reuse_lookup import ReuseRecord


class _Cap:
    def __init__(self, cid, block_ids):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = "REUSE"
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Made Up"
    product_id = "made-up"
    vertical = "made_up"
    summary = "A made-up product."


@pytest.mark.parametrize(
    "cap, block, shelf",
    [
        ("zorvex_reconcile", "zorvex_ledger", {"grid_panel"}),
        ("quillon_survey", "quillon_registry", {"pagereader", "grid_panel"}),
    ],
)
def test_a_block_the_store_confirms_is_known_though_the_shelf_lags(monkeypatch, cap, block, shelf):
    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def store_says_present(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=True, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(), _Plan(_Cap(cap, [block])), store_ids=set(shelf), reuse_http_get=store_says_present
    )

    item = compiled.inventory[0]
    assert item.verified_present == [block]
    assert item.missing == []
    assert compiled.missing_reuse == []
    assert block in compiled.store_ids
    registry_line = compiled.text.split("Store registry (exact ids, verified): ", 1)[1].splitlines()[0]
    assert block in registry_line.split(", ")


@pytest.mark.parametrize("block", ["zorvex_ledger", "quillon_registry"])
def test_a_block_the_store_does_not_confirm_and_the_shelf_lacks_is_not_known(monkeypatch, block):
    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def store_is_silent(block_id, base_url=None):
        return None

    compiled = compile_brief(
        _Blueprint(), _Plan(_Cap("c", [block])), store_ids={"grid_panel"}, reuse_http_get=store_is_silent
    )

    assert compiled.inventory[0].verified_present == []
    assert block not in compiled.store_ids
    assert compiled.store_ids == ["grid_panel"]
