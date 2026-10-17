"""Per-claim layer labels (Phase 3.1/3.3/3.4).

One answer synthesizes one claim from many sources; a source-level label
cannot tell the client which sentence came from layer 1 and which from
layer 3. Every CLAIM therefore carries its own layer (1-4) and its own
precedence entry. An unlabeled claim fails closed with ``unlabeled_claim``;
there is no fifth class.

Human labels: 1 "Certified" / 2 "Your documents" / 3 "Your formula" /
4 "Your procedure".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.cerebrum_product_kernel.precedence import (
    PRECEDENCE_VERSION,
    DivergenceRecord,
)

HUMAN_LAYER_LABELS = {
    1: "Certified",
    2: "Your documents",
    3: "Your formula",
    4: "Your procedure",
}

UNLABELED_CLAIM = "unlabeled_claim"


class ClaimLabelError(ValueError):
    """A named refusal from the claim-label enforcement."""


@dataclass(frozen=True)
class Claim:
    """One claim of an answer, labeled with its authority layer.

    ``precedence`` is the claim's own divergence entry (Phase 2.5): when
    this claim's layer won over another layer, the record carries both
    values. A claim with no layer is not a Claim — enforcement refuses it.
    """

    text: str
    layer: int
    object_id: str
    precedence: Optional[DivergenceRecord] = None

    def __post_init__(self) -> None:
        if self.layer not in HUMAN_LAYER_LABELS:
            raise ClaimLabelError(
                f"{UNLABELED_CLAIM}: claim layer must be 1-4, got {self.layer!r}"
            )

    @property
    def human_label(self) -> str:
        return HUMAN_LAYER_LABELS[self.layer]

    def to_dict(self) -> Dict[str, Any]:
        enforce_claim_labels([self])  # a stripped claim can never be emitted
        return {
            "text": self.text,
            "layer": self.layer,
            "label": self.human_label,
            "object_id": self.object_id,
            "precedence": self.precedence.to_dict() if self.precedence else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Claim":
        layer = data.get("layer")
        if layer is None or int(layer) not in HUMAN_LAYER_LABELS:
            raise ClaimLabelError(
                f"{UNLABELED_CLAIM}: claim has no valid layer label "
                f"({layer!r})"
            )
        prec = data.get("precedence")
        return cls(
            text=str(data.get("text") or ""),
            layer=int(layer),
            object_id=str(data.get("object_id") or ""),
            precedence=DivergenceRecord(**prec) if prec else None,
        )


@dataclass(frozen=True)
class LabeledAnswer:
    """An answer whose every claim carries its own layer + precedence."""

    claims: List[Claim] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "precedence_version": PRECEDENCE_VERSION,
            "claims": [claim.to_dict() for claim in self.claims],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LabeledAnswer":
        raw = data.get("claims")
        if raw is None:
            raise ClaimLabelError(
                f"{UNLABELED_CLAIM}: answer record has no claims section"
            )
        return cls(claims=[Claim.from_dict(c) for c in raw])


def enforce_claim_labels(claims: List[Claim]) -> None:
    """3.4: an unlabeled claim fails closed. Every claim must carry a layer;
    a claim's layer must be 1-4 (no fifth class)."""
    for claim in claims:
        layer = getattr(claim, "layer", None)
        if layer is None or layer not in HUMAN_LAYER_LABELS:
            raise ClaimLabelError(
                f"{UNLABELED_CLAIM}: claim {getattr(claim, 'text', '')[:40]!r} "
                "has no layer label — refusing to emit"
            )
