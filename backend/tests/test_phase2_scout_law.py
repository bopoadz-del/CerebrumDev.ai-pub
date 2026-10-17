"""The Phase 2 scout law (docs/PHASE2_SCOUT_LAW.md) is ratified and immutable.
Phase 2 = the first scouting prompt: a factory mission run ONCE per domain.
Subscriptions never trigger scouts — paid subscribers unlock the scout's
certified outputs via require_entitled. "Phase 2 enriches the system.
It does not redesign Phase 1." — the Founder's words, pinned.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = ROOT / "docs" / "PHASE2_SCOUT_LAW.md"
DOC_SHA256 = "40cd81e8e2bfff2bb3dd029049e1147c3fae420d27c4705f824b4fd99e518c93"


def test_phase2_scout_law_is_immutable():
    digest = hashlib.sha256(DOC_PATH.read_bytes()).hexdigest()
    assert digest == DOC_SHA256


def test_enrichment_not_redesign_is_pinned():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Phase 2 enriches the system. It does not redesign Phase 1." in text


def test_subscription_never_launches_a_scout():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "The subscription never launches a scout" in text
    assert "once per domain" in text


def test_the_two_extractions_are_separated():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Donor scout" in text
    assert "Customer-org extraction" in text
    assert "never conflate" in text
