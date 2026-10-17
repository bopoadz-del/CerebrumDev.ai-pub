"""The commercial tier model (docs/COMMERCIAL_TIERS.md) is ratified and immutable.
External names are Cerebrum Free / Pro / Team; "Phase" names stay internal.
The launch amendment gives free users a tiny upload + tiny RAG trial (3 docs,
7-day index, honest expiry). The boundary: Free understands the domain;
Paid understands your organisation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = ROOT / "docs" / "COMMERCIAL_TIERS.md"
DOC_SHA256 = "f563ec1718786e673de1907aacf8701a60bbfe5313f37f4bcba50b420572e5d5"


def test_commercial_tiers_are_immutable():
    digest = hashlib.sha256(DOC_PATH.read_bytes()).hexdigest()
    assert digest == DOC_SHA256


def test_commercial_names_are_external_phases_are_internal():
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in ("Cerebrum Free", "Cerebrum Pro", "Cerebrum Team"):
        assert name in text
    assert "Customers never see the word" in text


def test_launch_trial_is_ratified():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "three documents" in text
    assert "seven-day private index" in text
    assert "it understands my document" in text


def test_the_boundary_is_pinned():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Free understands the domain. Paid understands your organisation." in text


def test_registration_is_not_a_paid_benefit():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Registration exists for free users too" in text
