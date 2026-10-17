"""Four-layer authority precedence as versioned data, logged, never model-decided.

Phase 2 of the governed-layers build. The ladder (4 procedures > 3 formulas
> 2 documents > 1 certified) is DATA: it ships as a versioned JSON file and
is loaded at resolution time, never baked into prompt text. Resolution is
pure code — the model is TOLD the winner; it is never asked to choose.

Every resolution returns a divergence record, not just a winner: when a
client-taught formula (L3) shadows the certified formula (L1) by id, BOTH
results are computed and logged so the client can answer "what would the
certified formula have said?". For text layers the losing value is the
losing excerpt, not a number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

PRECEDENCE_VERSION = "precedence.v1"
LADDER_FILENAME = "precedence_ladder.v1.json"

LAYER_LABELS = {1: "certified", 2: "documents", 3: "formulas", 4: "procedures"}

#: Rule ids that appear in divergence records.
RULE_LADDER = "precedence.v1.ladder"
RULE_FORMULA_SHADOW = "precedence.v1.formula_shadow"


class PrecedenceError(ValueError):
    """A named refusal from the precedence engine."""


def default_ladder() -> Dict[int, int]:
    """The shipped ladder: 4 procedures > 3 formulas > 2 documents > 1 certified."""
    return {1: 1, 2: 2, 3: 3, 4: 4}


def load_ladder(path: Optional[Path] = None) -> Dict[int, int]:
    """Load the versioned rank table. ``path`` is an injection seam for tests
    and for P2 (invert the table and assert the suite goes RED)."""
    if path is None:
        path = Path(__file__).resolve().parent / LADDER_FILENAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != PRECEDENCE_VERSION:
        raise PrecedenceError(
            f"ladder_schema_mismatch: expected {PRECEDENCE_VERSION!r}, "
            f"got {raw.get('schema')!r}"
        )
    ladder: Dict[int, int] = {}
    for entry in raw.get("rank", []):
        layer = int(entry["layer"])
        rank = int(entry["rank"])
        if layer not in LAYER_LABELS:
            raise PrecedenceError(f"ladder_unknown_layer: {layer}")
        ladder[layer] = rank
    if set(ladder) != set(LAYER_LABELS):
        raise PrecedenceError(
            f"ladder_incomplete: rank must cover layers 1-4, got {sorted(ladder)}"
        )
    return ladder


@dataclass(frozen=True)
class LayerObject:
    """One object answering the ask, tagged with its authority layer.

    Layer 1 (certified) is tenant-agnostic; layers 2-4 are tenant-scoped.
    ``result`` carries the computed value for formula layers; ``excerpt``
    carries the text evidence for document/procedure layers.
    """

    object_id: str
    layer: int
    tenant_id: Optional[str] = None
    result: Any = None
    excerpt: str = ""
    evaluate: Optional[Callable[[], Any]] = None

    def __post_init__(self) -> None:
        if self.layer not in LAYER_LABELS:
            raise PrecedenceError(
                f"unknown_layer: layer must be 1..4, got {self.layer!r}"
            )
        if self.layer == 1 and self.tenant_id:
            raise PrecedenceError(
                "certified_layer_tenant_scoped: layer 1 is tenant-agnostic"
            )
        if self.layer != 1 and not self.tenant_id:
            raise PrecedenceError(
                "tenant_layer_unscoped: layers 2-4 must carry a tenant_id"
            )


@dataclass(frozen=True)
class DivergenceRecord:
    """What the loser would have said — the part a winner-only log hides."""

    winner_layer: int
    loser_layer: int
    rule_id: str
    winner_object_id: str
    winner_result: Any
    loser_object_id: str
    loser_result: Any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "winner_layer": self.winner_layer,
            "loser_layer": self.loser_layer,
            "rule_id": self.rule_id,
            "winner_object_id": self.winner_object_id,
            "winner_result": self.winner_result,
            "loser_object_id": self.loser_object_id,
            "loser_result": self.loser_result,
        }


@dataclass(frozen=True)
class PrecedenceVerdict:
    """The winner plus every divergence it implies. Never just a winner."""

    winner: LayerObject
    records: List[DivergenceRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "precedence_version": PRECEDENCE_VERSION,
            "winner_layer": self.winner.layer,
            "winner_object_id": self.winner.object_id,
            "divergences": [record.to_dict() for record in self.records],
        }


def _value_of(obj: LayerObject) -> Any:
    """The object's evidence: a computed result for formulas, the excerpt for
    text layers."""
    if obj.evaluate is not None:
        return obj.evaluate()
    if obj.result is not None:
        return obj.result
    return obj.excerpt


def resolve_precedence(
    objects: Sequence[LayerObject],
    ladder: Optional[Dict[int, int]] = None,
) -> PrecedenceVerdict:
    """Enforce the ladder in code and log every divergence.

    The highest-ranked object wins; each loser produces a divergence record
    carrying BOTH sides' values (computed for formulas, excerpt for text).
    The caller tells the model the winner — the model is never asked to
    choose, and this function has no model path.
    """
    if not objects:
        raise PrecedenceError("precedence_no_objects: resolution needs an object set")
    ladder = ladder if ladder is not None else load_ladder()
    ranked = sorted(objects, key=lambda o: ladder[o.layer], reverse=True)
    winner = ranked[0]
    records: List[DivergenceRecord] = []
    for loser in ranked[1:]:
        records.append(
            DivergenceRecord(
                winner_layer=winner.layer,
                loser_layer=loser.layer,
                rule_id=RULE_LADDER,
                winner_object_id=winner.object_id,
                winner_result=_value_of(winner),
                loser_object_id=loser.object_id,
                loser_result=_value_of(loser),
            )
        )
    return PrecedenceVerdict(winner=winner, records=records)


def resolve_formula_by_id(
    formula_id: str,
    objects: Sequence[LayerObject],
    ladder: Optional[Dict[int, int]] = None,
) -> PrecedenceVerdict:
    """Formula shadowing (2.4): taught (L3) shadows certified (L1) BY ID.

    Resolution happens here, at lookup, in code. When the winner is an L3
    shadowing an L1 with the same id, the record carries BOTH computed
    results under the formula_shadow rule id — the client can always answer
    "what would the certified formula have said?".
    """
    matches = [o for o in objects if o.object_id == formula_id]
    if not matches:
        raise PrecedenceError(
            f"formula_not_found: no object with id {formula_id!r}"
        )
    verdict = resolve_precedence(matches, ladder=ladder)
    certified = [o for o in matches if o.layer == 1]
    if verdict.winner.layer == 3 and certified:
        taught = verdict.winner
        base = certified[0]
        shadow = DivergenceRecord(
            winner_layer=3,
            loser_layer=1,
            rule_id=RULE_FORMULA_SHADOW,
            winner_object_id=taught.object_id,
            winner_result=_value_of(taught),
            loser_object_id=base.object_id,
            loser_result=_value_of(base),
        )
        return PrecedenceVerdict(
            winner=verdict.winner,
            records=[shadow, *verdict.records],
        )
    return verdict
