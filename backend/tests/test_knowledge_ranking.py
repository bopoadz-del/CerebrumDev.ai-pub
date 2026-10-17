"""Knowledge retrieval ranks by the shared credibility ladder.

CERTIFIED (1) must outrank QUARANTINE (5) at equal relevance. The previous
sort used ``-(credibility_tier)``, which inverted the documented ladder
(lower int = more credible).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.blocks import _knowledge as kb
from app.core.credibility import CredibilityScorer, CredibilityTier

# Dual-register pin. This string is copied identically into Cerebrum-Blocks
# ``tests/blocks/test_credibility_ladder_parity.py``. A unilateral edit of
# the tier map or CERTIFIED_MIN_ACCURACY fails CI unless BOTH copies of this
# literal are updated together.
CREDIBILITY_LADDER_LITERAL = (
    "CERTIFIED=1,OPERATIONAL=2,EXPERIMENTAL=3,UNVERIFIED=4,QUARANTINE=5;"
    "CERTIFIED_MIN_ACCURACY=0.95"
)


def _parse_credibility_ladder_literal(literal: str) -> tuple[dict[str, int], float]:
    """Split the shared pin into a tier map and CERTIFIED_MIN_ACCURACY."""
    tier_blob, acc_blob = literal.split(";")
    tiers = {}
    for item in tier_blob.split(","):
        name, raw = item.split("=")
        tiers[name] = int(raw)
    acc_name, acc_raw = acc_blob.split("=")
    assert acc_name == "CERTIFIED_MIN_ACCURACY"
    return tiers, float(acc_raw)


def _temp_kb(tmp_path: Path, monkeypatch, entries):
    kb_file = tmp_path / "kb.json"
    kb_file.write_text(
        json.dumps({"schema_version": "1", "kb_version": "test", "entries": entries}),
        encoding="utf-8",
    )
    monkeypatch.setenv(kb._KB_OVERRIDE_ENV, str(kb_file))
    kb._KB_CACHE = None
    kb._KB_MTIME = 0.0
    return kb_file


def _entry(eid, tier, **extra):
    return {
        "id": eid,
        "type": "rule",
        "title": "asphalt laying temperature minimum",
        "statement": "Asphalt must be laid above the minimum temperature.",
        "credibility_tier": tier,
        "applicability": {"applies_to": ["construction.roads"]},
        **extra,
    }


def test_knowledge_ranking_and_ladder_match_shared_literal(tmp_path, monkeypatch):
    """Ranking + CredibilityTier + CERTIFIED_MIN_ACCURACY pin one shared literal."""
    tiers, certified_min = _parse_credibility_ladder_literal(CREDIBILITY_LADDER_LITERAL)
    for name, value in tiers.items():
        assert int(getattr(CredibilityTier, name)) == value, name
    assert CredibilityScorer.CERTIFIED_MIN_ACCURACY == certified_min
    assert tiers["CERTIFIED"] < tiers["QUARANTINE"]
    assert kb.credibility_rank({"credibility_tier": tiers["CERTIFIED"]}) < (
        kb.credibility_rank({"credibility_tier": tiers["QUARANTINE"]})
    )
    _temp_kb(
        tmp_path,
        monkeypatch,
        [
            _entry("roads.quarantine", tiers["QUARANTINE"]),
            _entry("roads.certified", tiers["CERTIFIED"]),
        ],
    )
    results = kb.search_knowledge("asphalt laying temperature minimum", top_k=2)
    assert [r["id"] for r in results] == ["roads.certified", "roads.quarantine"]
    assert results[0]["credibility_tier"] == tiers["CERTIFIED"]
    assert results[1]["credibility_tier"] == tiers["QUARANTINE"]


def test_credibility_ladder_lower_int_is_more_credible():
    """Knowledge retrieval and the credibility module share one integer ladder."""
    assert int(CredibilityTier.CERTIFIED) == 1
    assert int(CredibilityTier.OPERATIONAL) == 2
    assert int(CredibilityTier.EXPERIMENTAL) == 3
    assert int(CredibilityTier.UNVERIFIED) == 4
    assert int(CredibilityTier.QUARANTINE) == 5
    assert CredibilityTier.CERTIFIED < CredibilityTier.QUARANTINE
    assert kb.credibility_rank({"credibility_tier": int(CredibilityTier.CERTIFIED)}) < (
        kb.credibility_rank({"credibility_tier": int(CredibilityTier.QUARANTINE)})
    )


def test_certified_ranks_before_quarantine_at_equal_relevance(tmp_path, monkeypatch):
    """Two otherwise-equal hits: CERTIFIED must return first, QUARANTINE second."""
    _temp_kb(
        tmp_path,
        monkeypatch,
        [
            _entry("roads.quarantine", int(CredibilityTier.QUARANTINE)),
            _entry("roads.certified", int(CredibilityTier.CERTIFIED)),
        ],
    )
    results = kb.search_knowledge("asphalt laying temperature minimum", top_k=2)
    assert [r["id"] for r in results] == ["roads.certified", "roads.quarantine"]
    assert results[0]["credibility_tier"] == int(CredibilityTier.CERTIFIED)
    assert results[1]["credibility_tier"] == int(CredibilityTier.QUARANTINE)


def test_equal_relevance_resolved_by_credibility_tier(tmp_path, monkeypatch):
    """At equal token overlap, lower tier int (higher authority) wins."""
    _temp_kb(
        tmp_path,
        monkeypatch,
        [
            _entry("roads.operational", int(CredibilityTier.OPERATIONAL)),
            _entry("roads.unverified", int(CredibilityTier.UNVERIFIED)),
        ],
    )
    results = kb.search_knowledge("asphalt laying temperature minimum", top_k=2)
    assert [r["id"] for r in results] == ["roads.operational", "roads.unverified"]
