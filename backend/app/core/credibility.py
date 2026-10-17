"""Credibility taxonomy + scorer.

Dual-registered from Cerebrum-Blocks ``app/core/credibility.py``. The integer
ladder is the ranking contract: retrieval, promotion, and surfacing all share
these values. Do not invert the documented direction to match a sort bug.

Realises the "5-Tier Credibility System" referenced in the architecture
docs. The doc text says "5-tier" but only enumerates 4 user-facing tiers
(Certified → Operational → Experimental → Unverified); we add an explicit
QUARANTINE tier as the 5th catch-all for items that have actively failed
validation, so callers never have to special-case "demoted to nothing".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Literal, Optional


class CredibilityTier(IntEnum):
    """Five-tier credibility ladder. Lower int = more credible."""

    CERTIFIED = 1  # Validated against real project data, ≥95% accuracy
    OPERATIONAL = 2  # Used in production, ≥85% accuracy
    EXPERIMENTAL = 3  # Beta — staff visibility only
    UNVERIFIED = 4  # New, no track record
    QUARANTINE = 5  # Failed validation; do not surface to users

    def __str__(self) -> str:
        # Python 3.11+ changed IntEnum.__str__ to render just the int.
        # Surface the qualified name in logs/repr-ish contexts (tests +
        # operators rely on it) while keeping IntEnum's numeric semantics.
        return f"{self.__class__.__name__}.{self.name}"


@dataclass
class CredibilityRecord:
    """Persistable record describing one item's current credibility."""

    item_id: str  # formula_id or recommendation_id
    tier: CredibilityTier = CredibilityTier.UNVERIFIED
    accuracy: float = 0.0  # rolling 0..1
    sample_size: int = 0
    last_updated: float = field(default_factory=time.time)
    promotion_history: List[Dict[str, Any]] = field(default_factory=list)
    accuracy_at_promotion: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "tier": int(self.tier),
            "tier_name": self.tier.name,
            "accuracy": self.accuracy,
            "sample_size": self.sample_size,
            "last_updated": self.last_updated,
            "promotion_history": list(self.promotion_history),
            "accuracy_at_promotion": self.accuracy_at_promotion,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CredibilityRecord":
        accuracy = float(data.get("accuracy", 0.0))
        return cls(
            item_id=data["item_id"],
            tier=CredibilityTier(int(data.get("tier", CredibilityTier.UNVERIFIED))),
            accuracy=accuracy,
            sample_size=int(data.get("sample_size", 0)),
            last_updated=float(data.get("last_updated", time.time())),
            promotion_history=list(data.get("promotion_history", [])),
            accuracy_at_promotion=float(data.get("accuracy_at_promotion", accuracy)),
        )


class CredibilityScorer:
    """Pure-function scorer + promotion evaluator.

    Thresholds are encoded inline so callers cannot drift apart on what
    "Certified" means.
    """

    CERTIFIED_MIN_ACCURACY = 0.95
    CERTIFIED_MIN_SAMPLES = 100

    OPERATIONAL_MIN_ACCURACY = 0.85
    OPERATIONAL_MIN_SAMPLES = 30

    EXPERIMENTAL_MIN_ACCURACY = 0.70
    EXPERIMENTAL_MIN_SAMPLES = 10

    QUARANTINE_MAX_ACCURACY = 0.50
    QUARANTINE_MIN_SAMPLES = 10

    def score(self, accuracy: float, sample_size: int) -> CredibilityTier:
        accuracy = max(0.0, min(1.0, float(accuracy)))
        sample_size = max(0, int(sample_size))

        if sample_size < self.EXPERIMENTAL_MIN_SAMPLES:
            return CredibilityTier.UNVERIFIED

        if (
            accuracy < self.QUARANTINE_MAX_ACCURACY
            and sample_size >= self.QUARANTINE_MIN_SAMPLES
        ):
            return CredibilityTier.QUARANTINE

        if (
            accuracy >= self.CERTIFIED_MIN_ACCURACY
            and sample_size >= self.CERTIFIED_MIN_SAMPLES
        ):
            return CredibilityTier.CERTIFIED

        if (
            accuracy >= self.OPERATIONAL_MIN_ACCURACY
            and sample_size >= self.OPERATIONAL_MIN_SAMPLES
        ):
            return CredibilityTier.OPERATIONAL

        if (
            accuracy >= self.EXPERIMENTAL_MIN_ACCURACY
            and sample_size >= self.EXPERIMENTAL_MIN_SAMPLES
        ):
            return CredibilityTier.EXPERIMENTAL

        return CredibilityTier.UNVERIFIED

    def evaluate_promotion(
        self,
        record: CredibilityRecord,
        accuracy: float,
        sample_size: int,
        reason: Optional[str] = None,
    ) -> CredibilityRecord:
        previous_tier = record.tier
        new_tier = self.score(accuracy, sample_size)

        record.accuracy = max(0.0, min(1.0, float(accuracy)))
        record.sample_size = max(0, int(sample_size))
        record.last_updated = time.time()

        if new_tier != previous_tier:
            record.tier = new_tier
            record.accuracy_at_promotion = record.accuracy
            record.promotion_history.append(
                {
                    "from": int(previous_tier),
                    "from_name": previous_tier.name,
                    "to": int(new_tier),
                    "to_name": new_tier.name,
                    "at": record.last_updated,
                    "reason": reason or self._default_reason(previous_tier, new_tier),
                    "accuracy": record.accuracy,
                    "sample_size": record.sample_size,
                }
            )
        return record

    def detect_drift(
        self,
        record: CredibilityRecord,
        current_accuracy: float,
        *,
        min_samples: int = 20,
        threshold: float = 0.10,
    ) -> bool:
        if record.sample_size < int(min_samples):
            return False
        baseline = float(record.accuracy_at_promotion)
        delta = baseline - float(current_accuracy)
        return delta >= float(threshold)

    @staticmethod
    def _default_reason(prev: CredibilityTier, new: CredibilityTier) -> str:
        # Lower int = more credible, so a drop in the int is a promotion.
        if int(new) < int(prev):
            return f"promoted from {prev.name} to {new.name}"
        if int(new) > int(prev):
            return f"demoted from {prev.name} to {new.name}"
        return "tier unchanged"

    def should_surface(
        self,
        tier: CredibilityTier,
        audience: Literal["user", "staff"],
    ) -> bool:
        if audience not in ("user", "staff"):
            raise ValueError(f"audience must be 'user' or 'staff', got {audience!r}")
        if tier in (CredibilityTier.CERTIFIED, CredibilityTier.OPERATIONAL):
            return True
        if tier == CredibilityTier.EXPERIMENTAL:
            return audience == "staff"
        if tier == CredibilityTier.UNVERIFIED:
            return audience == "staff"
        return False


__all__ = [
    "CredibilityTier",
    "CredibilityRecord",
    "CredibilityScorer",
]
