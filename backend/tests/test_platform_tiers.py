"""The platform tier law (docs/PLATFORM_TIERS.md) is ratified and immutable —
same doctrine as the other golds: ratify a new version, never edit in place.
Phase 1 freemium ships the body + v0.1 brain with honestly-labeled candidate
packs; Phase 2 (paid, later) is the exhaustive scout and domain_approved packs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIERS_PATH = ROOT / "docs" / "PLATFORM_TIERS.md"
TIERS_SHA256 = "4e1dab07be821a5d10714b3b9f535c06b0e0ffb0dc2a5986e9ae0b18294faf2d"


def test_platform_tiers_are_immutable():
    digest = hashlib.sha256(TIERS_PATH.read_bytes()).hexdigest()
    assert digest == TIERS_SHA256


def test_tier_law_is_pinned():
    text = TIERS_PATH.read_text(encoding="utf-8")
    assert "Phase 1 — FREEMIUM" in text
    assert "Phase 2 — PAID (later)" in text
    assert "domain_approved" in text          # paid = certified
    assert "require_entitled" in text        # billing gate enforces the line
    assert "Firing Phase 2 early" in text    # the Founder's deferral is law
