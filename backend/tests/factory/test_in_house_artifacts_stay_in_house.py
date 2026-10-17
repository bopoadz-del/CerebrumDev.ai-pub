"""The build's own record is not part of the product.

A live export shipped ``build_ledger.jsonl`` — 189 events, ~86KB of role
transitions, gate verdicts and the coding agent's narration — and
``product-dna/generation_manifest.json``, which is internal generation
metadata. The customer gets the platform, not the transcript of how it was
made.

Both exporters share one rule set (``is_exported``), so this covers the zip
and the cerebrum-builds push together. That matters: the two used to differ,
and the one that was not checked is the one that leaked.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.builds_push import is_exported

#: What stays in house.
IN_HOUSE = (
    "build_ledger.jsonl",
    "product-dna/generation_manifest.json",
    "docs/writer_prompt.txt",
    "docs/coder_brief.md",
    "docs/coder_receipt.json",
    "docs/writer_argv.json",
    "docs/writer_progress.jsonl",
    "docs/writer_progress.log",
    "docs/build_provenance.json",
)

#: What a customer must still receive. Listed explicitly because the cheapest
#: way to satisfy the rule above is to over-exclude, and product-dna is
#: mostly the documentation the pilot is bought for.
DELIVERED = (
    "MANIFEST.json",
    "README.md",
    "Dockerfile",
    "docs/provenance/provenance.json",
    "docs/openapi.json",
    "app/main.py",
    "app/actions/any_capability.py",
    "app/observability.py",
    "scripts/acceptance.py",
    "scripts/backup.sh",
    "tests/test_negative_floor.py",
    "product-dna/architecture.json",
    "product-dna/entity_model.json",
    "product-dna/action_catalog.json",
    "product-dna/known_limitations.json",
)


class TestTheBuildsOwnRecordStaysInHouse:
    def test_the_ledger_does_not_ship(self):
        assert not is_exported(Path("build_ledger.jsonl"))

    def test_the_generation_manifest_does_not_ship(self):
        assert not is_exported(Path("product-dna/generation_manifest.json"))

    def test_every_in_house_artifact_is_withheld(self):
        leaked = [rel for rel in IN_HOUSE if is_exported(Path(rel))]
        assert not leaked, f"shipped to the customer: {leaked}"

    def test_the_ledger_is_withheld_at_any_depth(self):
        """The ledger is excluded by name; it is the Factory's record
        wherever it sits."""
        assert not is_exported(Path("sessions/x/build_ledger.jsonl"))

    def test_the_rest_are_withheld_by_exact_path(self):
        """By path, not by bare name.

        ``build_provenance.json`` is distinctive today, but excluding by
        bare name is how an exclusion list starts eating a product's own
        files later -- a product is free to have its own docs/ or a file
        called coder_brief.md somewhere that is genuinely its content.
        """
        assert is_exported(Path("app/docs/coder_brief.md"))
        assert is_exported(Path("product-dna/build_provenance.json"))


class TestTheProductItselfStillShips:
    def test_everything_a_customer_needs_is_still_exported(self):
        missing = [rel for rel in DELIVERED if not is_exported(Path(rel))]
        assert not missing, f"over-excluded: {missing}"

    def test_the_rest_of_product_dna_is_untouched(self):
        """Only the generation manifest leaves; product-dna is the
        documentation the pilot is bought for."""
        kept = [
            "product-dna/architecture.json",
            "product-dna/capability_resolution.json",
            "product-dna/security_policy.json",
            "product-dna/test_catalog.json",
            "product-dna/README.md",
        ]
        assert all(is_exported(Path(rel)) for rel in kept)


def test_caches_and_bytecode_are_still_excluded():
    """The exclusions that were already there keep working."""
    for rel in ("app/__pycache__/x.pyc", ".git/config", "data/platform.db"):
        assert not is_exported(Path(rel)), rel
