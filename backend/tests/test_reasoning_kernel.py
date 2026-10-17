"""The Cerebrum Reasoning Kernel gold (docs/REASONING_KERNEL.md) is ratified and
immutable — same doctrine as the Universal Kernel gold and the Product Delivery
Standard: ratify a new version, never edit in place.

Factory-only artifact: the brain to the Universal Kernel's body. It is NOT
shipped to Cerebrum-Blocks or to products — when the Reasoning Kernel mission
fires, briefs are rendered from this gold, and only its outputs cross repos.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "docs" / "REASONING_KERNEL.md"
# Re-ratified 2026-09-17 (owner directive, commit bb0247ec): Kimi framing
# removed, neutral kernel framing + Universal Kernel noted as in place.
GOLD_SHA256 = "f75d5f532899f5e459e1b485abbe6c96b455c2357a95d8fefd370dd88e1da747"


def test_reasoning_kernel_gold_is_immutable():
    digest = hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest()
    assert digest == GOLD_SHA256, (
        "docs/REASONING_KERNEL.md changed — ratify a new version of the gold, "
        "never edit it in place"
    )


def test_gold_defines_the_two_kernels():
    text = GOLD_PATH.read_text(encoding="utf-8")
    assert "Universal Platform Kernel" in text  # the body (first gold)
    assert "Cerebrum Reasoning Kernel" in text  # the brain (this gold)


def test_gold_covers_all_eight_phases():
    text = GOLD_PATH.read_text(encoding="utf-8")
    for phase in range(1, 9):
        assert f"PHASE {phase}" in text


def test_gold_names_the_neutral_proof_domain():
    text = GOLD_PATH.read_text(encoding="utf-8")
    assert "equipment-maintenance" in text


def test_gold_keeps_llm_off_the_authority_seat():
    text = GOLD_PATH.read_text(encoding="utf-8")
    assert "must never become the authority" in text
