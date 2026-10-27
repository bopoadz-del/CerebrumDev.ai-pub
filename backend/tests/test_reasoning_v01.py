"""Gold #2.1 — the ownership correction and the v0.1 Standard Control Layer
mission are ratified and immutable, same doctrine as golds #1 and #2:
ratify a new version, never edit in place.

- docs/REASONING_KERNEL_OWNERSHIP.md  — the brain lives in CerebrumDev.ai;
  the Store supplies certified tools; Steward holds authority.
- docs/REASONING_KERNEL_V01_MISSION.md — the fire-ready Kimi mission for the
  v0.1 reasoning control layer (canonical in the factory; products receive
  pinned snapshots; the exhaustive scout is deferred to a later phase).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNERSHIP_PATH = ROOT / "docs" / "REASONING_KERNEL_OWNERSHIP.md"
V01_PATH = ROOT / "docs" / "REASONING_KERNEL_V01_MISSION.md"
# Re-ratified 2026-10-27 (ORDERS_0400Z, owner delegation for scrub-only changes):
# r4 scrub tokenised private names (Cerebrum-Steward->Projectsix,
# Cerebrum-FinanceOps->Projecttwo). Diff vs pre-scrub gold (mirror b79721bc) is
# name tokens only -- no semantic change.
OWNERSHIP_SHA256 = "077a8bbd6ca7922ee38aefaaf334c32183d4c5d34d027070cad8f1d1bf31d342"
V01_SHA256 = "fa7e274e124ffceeec44d3210c8d1421619d47233c41b763fe00ea36661ee4c0"


def test_ownership_correction_is_immutable():
    digest = hashlib.sha256(OWNERSHIP_PATH.read_bytes()).hexdigest()
    assert digest == OWNERSHIP_SHA256


def test_v01_mission_is_immutable():
    digest = hashlib.sha256(V01_PATH.read_bytes()).hexdigest()
    assert digest == V01_SHA256


def test_brain_is_canonical_in_the_factory():
    text = OWNERSHIP_PATH.read_text(encoding="utf-8")
    assert "owns the real Reasoning Kernel" in text
    assert "does not reason globally" in text  # the Store stays a warehouse


def test_v01_defers_the_scout_and_protects_the_store():
    text = V01_PATH.read_text(encoding="utf-8")
    assert "Do not perform the exhaustive multi-platform domain scout" in text
    assert "Do not modify Cerebrum-Blocks" in text


def test_v01_pins_the_standard_outcomes():
    text = V01_PATH.read_text(encoding="utf-8")
    for outcome in ("success", "dependency_required", "validation_error",
                    "permission_denied", "approval_required", "unsupported",
                    "conflict_detected", "execution_error"):
        assert outcome in text
