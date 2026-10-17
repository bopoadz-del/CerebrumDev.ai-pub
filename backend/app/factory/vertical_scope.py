"""Which blocks a vertical may attach.

Every platform the Factory builds is built for a vertical -- the blueprint's
own ``vertical`` field. Some Store blocks only make sense in some of them:
``drawing_qto`` takes quantities off construction drawings, and attaching it
to a hotel platform ships a part that can never be fed.

THE FACTORY HOLDS NO LIST OF DOMAINS. A block declares the verticals it
belongs to, as data on its own Store manifest::

    "verticals": ["construction", "interior_design", "architecture", ...]

and this module applies one rule to whatever it finds there:

    a block that declares ``verticals`` is eligible only for a blueprint
    whose vertical is one of them; a block that declares nothing is
    eligible everywhere.

So scoping another block, or opening one to a fifth vertical, is an edit in
the Store and never a change here. The rule is applied in the planner, the
one place block ids enter a build: COLLECTOR reads them from the plan and
CLONER reads them from COLLECTOR, so a block scoped out here is never cloned.

A scoped-out block is DROPPED from its capability, not failed. "It does not
get cloned" is the owner's instruction; refusing the whole plan because a
blueprint named a part that does not belong to its vertical would turn a
scoping rule into a build outage. The capability keeps its other blocks, or
falls to generation with none -- which COLLECTOR already reports as a gap.
Every drop is recorded on the plan with its reason: logged precedence, not a
silent edit.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

MANIFEST_KEY = "verticals"

#: What KIND of organisation it is, never WHICH domain. A blueprint's vertical
#: is free text from the architect -- "architecture offices", "interior design
#: office" -- while a block declares the domain itself. Stripping the
#: organisational noun is what lets the two meet without this file ever
#: learning a domain word.
_ORGANISATION_SUFFIXES = (
    "offices", "office", "firms", "firm", "practices", "practice",
    "companies", "company", "studios", "studio", "agencies", "agency",
    "platforms", "platform", "businesses", "business",
)


def normalize_vertical(vertical: object) -> str:
    """``"Architecture Offices"`` -> ``"architecture"``.

    Whole names only. An earlier guard in this repo split ``real_estate``
    into tokens and matched "a REAL outbound delivery"; nothing here is ever
    split into words and matched piecemeal.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(vertical or "").strip().lower()).strip("_")
    for suffix in _ORGANISATION_SUFFIXES:
        if slug.endswith("_" + suffix):
            return slug[: -(len(suffix) + 1)]
    return slug


def block_verticals(blocks_root: Optional[Path]) -> Dict[str, FrozenSet[str]]:
    """``{block_id: declared verticals}`` for every Store block that declares any.

    Blocks that declare nothing are absent from the result, which is what
    makes them eligible everywhere. An unreadable manifest is skipped: a
    scoping rule must not be the thing that takes the planner down.
    """
    out: Dict[str, FrozenSet[str]] = {}
    if not blocks_root:
        return out
    registry = Path(blocks_root) / "block_registry"
    if not registry.is_dir():
        return out
    for meta in sorted(registry.glob("*/block.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("vertical scope: unreadable manifest %s", meta, exc_info=True)
            continue
        declared = data.get(MANIFEST_KEY)
        if not isinstance(declared, list) or not declared:
            continue
        scope = frozenset(normalize_vertical(v) for v in declared if isinstance(v, str))
        scope = frozenset(v for v in scope if v)
        if scope:
            out[str(data.get("id") or meta.parent.name)] = scope
    return out


def scope_blocks(
    block_ids: Iterable[str],
    vertical: object,
    scopes: Dict[str, FrozenSet[str]],
) -> Tuple[List[str], List[Dict[str, object]]]:
    """``(kept, dropped)`` for one capability's blocks in one vertical."""
    wanted = normalize_vertical(vertical)
    kept: List[str] = []
    dropped: List[Dict[str, object]] = []
    for bid in block_ids:
        scope = scopes.get(bid)
        if scope is None or wanted in scope:
            kept.append(bid)
            continue
        dropped.append(
            {
                "block_id": bid,
                "vertical": wanted or "(none declared)",
                "allowed_verticals": sorted(scope),
                "reason": (
                    f"{bid} is scoped to {', '.join(sorted(scope))}; this "
                    f"platform's vertical is {wanted or 'not declared'!r}, so "
                    "it is not attached"
                ),
            }
        )
    return kept, dropped
