"""The Factory can tell a vouched-for block from an unproven one.

Every block.json in the Store carries ``trust_tier: "platform"`` -- all 164 of
them. So the only trust signal the Factory had was a constant, and it attached
a block nobody had certified exactly as readily as one that had survived the
Store's control-delete. The evidence was sitting in block_certifications.json,
which the Factory never opened.

"Not certified" means no evidence, never "known bad": the Store ships pilots
off the whole shelf. The point is that a product which labels a layer
*certified* must be able to point at something.
"""

from __future__ import annotations

import json

from app.factory.dual_registry import certified_ids, load_block_certifications
from app.factory.store_catalog import build_store_catalog, render_for_chat


def _store(tmp_path, blocks, certifications=None):
    for bid in blocks:
        d = tmp_path / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(
            json.dumps({"id": bid, "version": "1.0.0", "trust_tier": "platform"}),
            encoding="utf-8",
        )
    if certifications is not None:
        (tmp_path / "block_certifications.json").write_text(
            json.dumps({"blocks": certifications}), encoding="utf-8"
        )
    return tmp_path


class TestReadingTheStoreRecord:
    def test_only_certified_entries_count(self, tmp_path):
        root = _store(
            tmp_path,
            ["proven", "claimed", "unproven"],
            [
                {"block": "proven", "certified": True, "fixture": "a real PDF"},
                {"block": "claimed", "certified": False},
            ],
        )

        assert certified_ids(root) == {"proven"}
        assert load_block_certifications(root)["proven"]["fixture"] == "a real PDF"

    def test_a_missing_record_is_no_evidence_not_an_error(self, tmp_path):
        root = _store(tmp_path, ["alpha"], certifications=None)

        assert certified_ids(root) == set()

    def test_an_unreadable_record_is_no_evidence_not_an_error(self, tmp_path):
        root = _store(tmp_path, ["alpha"], certifications=[])
        (root / "block_certifications.json").write_text("{not json", encoding="utf-8")

        assert certified_ids(root) == set()


class TestTheCatalogCarriesIt:
    def test_certified_is_separate_from_cleared(self, tmp_path, monkeypatch):
        root = _store(
            tmp_path,
            ["proven", "unproven"],
            [{"block": "proven", "certified": True}],
        )
        monkeypatch.setattr(
            "app.factory.dual_registry.load_factory_shelf",
            lambda *a, **k: {},
        )

        catalog = build_store_catalog(root)

        # Clearance and certification answer different questions: a block can
        # be cleared to attach without anyone having proved it works.
        assert catalog["certified"] == ["proven"]

    def test_the_chat_is_told_what_is_proven_and_what_is_merely_unproven(self):
        text = render_for_chat({"blocks": ["a"], "certified": ["a"]})

        assert "CERTIFIED" in text
        assert "unproven, not unusable" in text

    def test_no_certifications_says_none_yet_rather_than_going_silent(self):
        text = render_for_chat({"blocks": ["a"], "certified": []})

        assert "(none yet)" in text


class TestTheArchitectIsToldWhichBlocksAreProven:
    def _block_list(self, monkeypatch, dual, proven):
        from app.factory import product_architect

        monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: dual)
        monkeypatch.setattr(product_architect, "_certified_ids", lambda: proven)
        captured = {}

        def _capture(messages):
            captured["system"] = messages[0]["content"]
            raise RuntimeError("stop after the prompt is built")

        monkeypatch.setattr(product_architect, "_llm_json_call", _capture)
        try:
            product_architect._draft_with_llm("a brief")
        except RuntimeError:
            pass
        return captured["system"]

    def test_certified_blocks_are_marked(self, monkeypatch):
        text = self._block_list(monkeypatch, ["proven", "unproven"], {"proven"})

        assert "- proven  [certified]" in text
        assert "- unproven\n" in text

    def test_the_mark_is_explained_so_it_is_a_preference_not_a_filter(self, monkeypatch):
        text = self._block_list(monkeypatch, ["proven", "unproven"], {"proven"})

        assert "unproven, not unusable" in text

    def test_nothing_certified_means_no_marks_and_no_note(self, monkeypatch):
        text = self._block_list(monkeypatch, ["a", "b"], set())

        assert "[certified]" not in text
