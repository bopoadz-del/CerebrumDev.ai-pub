"""Factory inventory declaration: ready verticals and honest draft notes."""

from __future__ import annotations


from app.factory.inventory import (
    inventory_drafting_note,
    ready_kit,
    vertical_is_excluded,
)
from app.factory.product_architect import draft_blueprint_from_brief


class TestReadyVerticals:
    def test_the_declared_five_map_to_store_kits(self):
        assert ready_kit("hotels") == "hotel_management"
        assert ready_kit("insurance") == "insurance"
        assert ready_kit("construction") == "construction"
        assert ready_kit("finance") == "finance"
        assert ready_kit("retail") == "retail"

    def test_excluded_verticals_are_named(self):
        for v in ("medical", "legal", "veterinary", "vet", "pharma"):
            assert vertical_is_excluded(v), v

    def test_ready_verticals_carry_no_warning(self):
        for v in ("hotels", "insurance", "construction", "finance", "retail"):
            assert inventory_drafting_note(v) == "", v

    def test_excluded_vertical_carries_the_stronger_note(self):
        note = inventory_drafting_note("veterinary_clinic")
        assert "no domain kit" in note
        assert "declared-ready" in note

    def test_unverified_vertical_gets_the_softer_note(self):
        note = inventory_drafting_note("agriculture")
        assert "not on the declared-ready list" in note


class TestDomainGaps:
    def test_generic_plumbing_only_is_a_domain_gap(self):
        from app.factory.inventory import domain_gaps

        gaps = domain_gaps(
            "retail",
            [
                {"capability_id": "orders", "block_ids": ["database", "workflow"]},
                {"capability_id": "products", "block_ids": ["retail_v2"]},
            ],
        )
        assert len(gaps) == 1
        assert gaps[0]["capability_id"] == "orders"
        assert "generic blocks only" in gaps[0]["note"]

    def test_domain_block_satisfies_the_capability(self):
        from app.factory.inventory import domain_gaps

        gaps = domain_gaps("insurance", [{"capability_id": "claims", "block_ids": ["bordereaux_ingest"]}])
        assert gaps == []

    def test_excluded_vertical_has_no_kit_gap_opinion(self):
        from app.factory.inventory import domain_gaps

        assert domain_gaps("veterinary", [{"capability_id": "x", "block_ids": ["database"]}]) == []


class TestDraftCarriesTheDeclaration:
    def test_excluded_vertical_draft_warns_instead_of_silent(self, monkeypatch):
        # No LLM: deterministic keyword drafting so the test is offline.
        monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
        bp = draft_blueprint_from_brief(
            "Build a platform for a veterinary clinic",
            vertical_hint="veterinary",
            use_llm=False,
            use_goldens=False,
        )
        assert bp.drafting_note and "no domain kit" in bp.drafting_note

    def test_ready_vertical_draft_has_no_inventory_warning(self, monkeypatch):
        monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
        bp = draft_blueprint_from_brief(
            "Build a platform for a retail chain",
            vertical_hint="retail",
            use_llm=False,
            use_goldens=False,
        )
        assert not bp.drafting_note or "inventory" not in bp.drafting_note
