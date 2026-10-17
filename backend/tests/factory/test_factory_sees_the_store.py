"""The Factory reads the Store's inventory instead of a copy of it.

What the Factory offered was a hand-written file: app/factory/shelves/
factory_blocks.json, 25 blocks with their kit assignments. The Store holds
136 blocks (every block_registry/<id>/block.json carries id, version and
trust_tier) and shelves 18 kits marked available. So the chat told a finance
customer there was no ready kit while block_store/kits/finance_ops sat there
marked available, carrying a chart-of-accounts governance block -- and the
coding agent wrote a five-entry chart of accounts from scratch.

Two questions were sharing one file. What the Store HOLDS is now read from
the Store. What the Factory has CLEARED to attach stays the Factory's own
statement, because nothing in the Store says which blocks are cleared for a
customer build -- all 136 are trust_tier "platform".
"""

from __future__ import annotations

import json

from app.factory.dual_registry import load_factory_shelf, shelf_from_store
from app.factory.kit_pack import kit_map_from_store, load_shelf_kit_map
from app.factory.store_catalog import store_catalog


def _store(tmp_path, blocks: dict, kits: dict):
    for bid, meta in blocks.items():
        d = tmp_path / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps(meta), encoding="utf-8")
    for kid, meta in kits.items():
        d = tmp_path / "block_store" / "kits" / kid
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


class TestTheShelfComesFromTheStore:
    def test_it_reads_id_version_and_trust_from_each_block(self, tmp_path):
        root = _store(
            tmp_path,
            {"alpha": {"id": "alpha", "version": "2.1.0", "trust_tier": "platform"}},
            {},
        )

        shelf = shelf_from_store(root)

        assert set(shelf) == {"alpha"}
        assert shelf["alpha"].version == "2.1.0"
        assert shelf["alpha"].trust_tier == "platform"
        assert shelf["alpha"].source == "cerebrum-blocks"

    def test_a_block_that_vouches_for_nothing_stays_unvouched(self, tmp_path):
        """compliance_gate refuses an empty tier; it is not ours to invent."""
        root = _store(tmp_path, {"alpha": {"id": "alpha", "version": "1"}}, {})

        assert shelf_from_store(root)["alpha"].trust_tier == ""

    def test_an_unreadable_store_yields_nothing_rather_than_guessing(self, tmp_path):
        assert shelf_from_store(tmp_path / "nowhere") == {}

    def test_the_real_store_is_larger_than_the_hand_written_shelf(self):
        assert len(shelf_from_store()) > len(load_factory_shelf())


class TestKitsComeFromTheStore:
    def test_only_kits_the_store_marks_available_are_read(self, tmp_path):
        root = _store(
            tmp_path,
            {},
            {
                "finance_ops": {"id": "finance_ops", "status": "available", "blocks": ["ledger"]},
                "half_built": {"id": "half_built", "status": "draft", "blocks": ["wip"]},
            },
        )

        mapping = kit_map_from_store(root)

        assert mapping == {"ledger": "finance_ops"}

    def test_the_template_kit_is_not_a_kit(self, tmp_path):
        root = _store(
            tmp_path, {}, {"_template": {"id": "_template", "status": "available", "blocks": ["x"]}}
        )

        assert kit_map_from_store(root) == {}


class TestClearanceIsStillTheFactorysStatement:
    def test_vendoring_is_unchanged_by_what_the_store_holds(self):
        """Kit FILES come from the Store by registry id. Renaming a product's kit to
        one the Factory cannot vendor would ship an empty kits/."""
        assert set(load_shelf_kit_map().values()) <= {"platform", "private_estate_operations"}

    def test_the_catalog_separates_what_is_held_from_what_is_cleared(self):
        catalog = store_catalog()

        assert len(catalog["store_blocks"]) > len(catalog["blocks"]), (
            "the Store holds more than the Factory has cleared, and the chat "
            "must be able to say so"
        )
        assert catalog["store_kits"], "the Store's kits must be visible"
        assert set(catalog["blocks"]) <= set(catalog["store_blocks"])

    def test_nothing_the_factory_refuses_became_attachable(self):
        """The guard, re-aimed at what the Factory actually decides.

        It used to assert that most of the Store read as uncleared, which
        was true while clearance was a 25-entry file in this repo. The owner
        moved clearance to the Store itself -- a block the Store publishes is
        attachable, because the Store is where blocks live and it changes
        daily. That deliberately clears the drives, which is the point: a
        pilot is meant to reach Google Drive.

        What survives is the part that is genuinely the Factory's call: the
        named refusals. Nothing on that list may be attachable, and the chat
        must still be able to name it rather than pretend it does not exist.
        """
        from app.factory.dual_registry import not_cleared_block_ids

        catalog = store_catalog()

        refused = set(not_cleared_block_ids())
        assert refused, "the Factory must still be able to refuse a block"
        assert not (refused & set(catalog["blocks"])), (
            "a refused block is attachable"
        )
        cleared_ids = {c["id"] for c in catalog["connectors"]}
        uncleared_ids = {c["id"] for c in catalog["not_cleared"]}
        assert not (cleared_ids & uncleared_ids)
        assert refused <= uncleared_ids, (
            "the chat must be able to name what the Factory refuses"
        )

    def test_every_refusal_carries_its_reason(self):
        """A deny-list without reasons grows silently and is never revisited."""
        from app.factory.dual_registry import not_cleared_block_ids

        for bid, reason in not_cleared_block_ids().items():
            assert reason and len(reason) > 20, bid


class TestThePublishedShelfIsTheOneThatCounts:
    """The Store is about to publish its shelf. When it does, the Factory
    takes it -- no edit here, no second opinion, nothing hardwired."""

    def _published(self, tmp_path, blocks):
        shelf = tmp_path / "shelves"
        shelf.mkdir(parents=True)
        (shelf / "factory_blocks.json").write_text(
            json.dumps({"schema_version": "factory_shelf.v1", "blocks": blocks}),
            encoding="utf-8",
        )
        return tmp_path

    def test_the_store_shelf_is_found_where_the_store_publishes_it(self, tmp_path):
        from app.factory.dual_registry import store_shelf_file

        root = self._published(tmp_path, [{"id": "alpha"}])

        assert store_shelf_file(root) == root / "shelves" / "factory_blocks.json"

    def test_nothing_published_yet_means_no_store_shelf(self, tmp_path):
        from app.factory.dual_registry import store_shelf_file

        assert store_shelf_file(tmp_path) is None

    def test_a_published_block_carries_its_kit_and_tier(self, tmp_path, monkeypatch):
        import app.factory.dual_registry as dual

        root = self._published(
            tmp_path,
            [
                {
                    "id": "finance_reconciliation",
                    "version": "1.0.0",
                    "kit": "finance_ops",
                    "trust_tier": "platform",
                }
            ],
        )
        monkeypatch.setattr(dual, "_default_blocks_root", lambda: root)

        shelf = load_factory_shelf()
        kits = load_shelf_kit_map()

        assert shelf["finance_reconciliation"].trust_tier == "platform"
        assert shelf["finance_reconciliation"].version == "1.0.0"
        assert kits["finance_reconciliation"] == "finance_ops"

    def test_a_kit_named_on_the_shelf_vendors_from_the_store(self, tmp_path):
        """find_kit_source already looks in the Store: publishing is enough."""
        from app.factory.kit_pack import find_kit_source

        kit = tmp_path / "block_store" / "kits" / "finance_ops"
        kit.mkdir(parents=True)
        (kit / "manifest.json").write_text('{"id": "finance_ops"}', encoding="utf-8")

        assert find_kit_source("finance_ops", tmp_path) == kit

    def test_an_unreadable_published_shelf_falls_back_rather_than_crashing(
        self, tmp_path, monkeypatch
    ):
        import app.factory.dual_registry as dual

        shelf = tmp_path / "shelves"
        shelf.mkdir(parents=True)
        (shelf / "factory_blocks.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(dual, "_default_blocks_root", lambda: tmp_path)

        assert load_factory_shelf(), "a broken publish must not empty the shelf"
