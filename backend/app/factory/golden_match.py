"""Route a drafted brief to a golden blueprint by STRUCTURE, never by words.

A golden is a blueprint on disk that declares the verticals it serves
(``serves_verticals``) -- opting in is data, not a Factory list. Its declared
structure is its capability ids, its serves_verticals, and the capabilities
declared by the Store kit that serves its vertical, when there is one.

A draft's structure is its capability ids, its vertical and the caller's
vertical hint. The two are compared by Jaccard similarity over ids. The best
golden wins only if it clears the configured threshold and is the unique best;
a tie means no golden and the product is built from the draft. Nothing here
reads the brief's text.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, FrozenSet, Iterable, List, Optional, Tuple

#: Minimum Jaccard overlap for a golden to replace a draft. Config, not code:
#: FACTORY_GOLDEN_MIN_OVERLAP overrides it.
DEFAULT_MIN_OVERLAP = 0.5


def min_overlap() -> float:
    raw = os.environ.get("FACTORY_GOLDEN_MIN_OVERLAP", "").strip()
    try:
        return float(raw) if raw else DEFAULT_MIN_OVERLAP
    except ValueError:
        return DEFAULT_MIN_OVERLAP


def _ids(values: Iterable[Any]) -> FrozenSet[str]:
    out = set()
    for v in values:
        s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
        if s:
            out.add(s)
    return frozenset(out)


def jaccard(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


@dataclass(frozen=True)
class Golden:
    path: Path
    name: str
    structure: FrozenSet[str]
    #: The verticals this golden declares it serves (its own serves_verticals).
    serves: FrozenSet[str] = frozenset()


def eligible_for(vertical: Any, candidates: Iterable[Golden]) -> List[Golden]:
    """The goldens that declare the user's chosen vertical. A golden never
    gives a product a vertical the user did not pick."""
    want = next(iter(_ids([vertical])), "")
    return [g for g in candidates if want and want in g.serves]


def draft_structure(blueprint: Any, vertical_hint: Optional[str] = None) -> FrozenSet[str]:
    caps = [getattr(c, "id", None) or getattr(c, "capability_id", None)
            for c in (getattr(blueprint, "capabilities", None) or [])]
    return _ids([*caps, getattr(blueprint, "vertical", None), vertical_hint])


def _kit_capabilities(vertical: str, store_root: Any) -> List[str]:
    from app.factory.store_kits import domain_kits, serving_kit

    kits = domain_kits(store_root)
    kit_id = serving_kit(vertical, kits)
    caps = (kits.get(kit_id) or {}).get("capabilities") if kit_id else None
    return [str(c) for c in caps] if isinstance(caps, list) else []


def goldens(blueprints_root: Path, store_root: Any = None) -> List[Golden]:
    from app.factory.blueprint import load_blueprint

    out: List[Golden] = []
    for path in sorted(Path(blueprints_root).rglob("*.yaml")):
        try:
            bp = load_blueprint(path)
        except Exception:  # noqa: BLE001 -- not a product blueprint
            continue
        if not bp.serves_verticals:
            continue
        structure = _ids([
            *(c.id for c in bp.capabilities),
            *bp.serves_verticals,
            *_kit_capabilities(bp.vertical, store_root),
        ])
        out.append(Golden(path=path, name=bp.product_name, structure=structure,
                          serves=_ids(bp.serves_verticals)))
    return out


def best_golden(
    draft: FrozenSet[str],
    candidates: Iterable[Golden],
    threshold: Optional[float] = None,
) -> Optional[Tuple[Golden, float]]:
    """The unique best-overlapping golden at or above the threshold, else None."""
    floor = min_overlap() if threshold is None else threshold
    scored = sorted(((jaccard(draft, g.structure), g) for g in candidates),
                    key=lambda pair: pair[0], reverse=True)
    if not scored or scored[0][0] < floor or scored[0][0] == 0.0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None  # a tie is not a decision; build from the draft
    return scored[0][1], scored[0][0]
