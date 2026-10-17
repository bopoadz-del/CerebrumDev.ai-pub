"""Factory inventory declaration: what the Store actually stocks.

The writer can only be honest about domain depth where the Store carries
domain content. Which kit serves a vertical, and whether the operator declared
it ready to build against, are facts the Store's kit manifests state about
themselves (``serves_verticals``, ``build_ready``) -- read here through
``store_kits``, never copied into a Factory table. A copy goes stale; a kit
the Store ships must not read as missing because the Factory's list predates
it. Everything not declared ready gets an honest inventory note on the draft --
never a silent fabrication of domain authority (live-factory lesson: a
veterinary-clinic draft carried 8 REUSE capabilities against generic plumbing
with zero domain content).
"""

from __future__ import annotations

from typing import Any, Optional

from app.factory.store_kits import (
    UNSUPPORTED,
    domain_blocks,
    domain_kits,
    resolve_vertical,
    slug,
)


def _slug(vertical: str) -> str:
    return slug(vertical)


def ready_kit(vertical: str, store_root: Any = None) -> Optional[str]:
    """The Store kit serving a vertical the operator declared ready, or None."""
    kits = domain_kits(store_root)
    status, kit, _ = resolve_vertical(vertical, kits)
    if status == UNSUPPORTED:
        return None
    return kit if kit and kits[kit].get("build_ready") is True else None


def vertical_is_excluded(vertical: str, store_root: Any = None) -> bool:
    """A Store kit excludes this vertical, or serves it and is NOT ready."""
    kits = domain_kits(store_root)
    status, kit, _ = resolve_vertical(vertical, kits)
    if status == UNSUPPORTED:
        return True
    return bool(kit) and kits[kit].get("build_ready") is False


def _ready_names(store_root: Any = None) -> str:
    kits = domain_kits(store_root)
    return ", ".join(sorted(k for k, m in kits.items() if m.get("build_ready") is True)) or "none"


def inventory_drafting_note(vertical: str, store_root: Any = None) -> str:
    """The honest note a draft carries when the vertical is not declared ready.

    A vertical whose serving kit the operator declared not ready gets the
    stronger wording: building it means the writer synthesizes domain
    authority the Store never certified.
    """
    s = _slug(vertical)
    if not s:
        return ""
    ready = _ready_names(store_root)
    status, kit, reason = resolve_vertical(s, domain_kits(store_root))
    if status == UNSUPPORTED:
        return (
            f"inventory: '{s}' is {UNSUPPORTED} — {reason}; no domain kit in "
            f"the Store builds it (declared-ready set: {ready})"
        )
    if vertical_is_excluded(s, store_root):
        return (
            f"inventory: no domain kit in the Store for '{s}' — this "
            f"vertical is outside the declared-ready set ({ready}); domain "
            "logic will be thin because the Store supplies no authoritative "
            "domain content"
        )
    if not ready_kit(s, store_root):
        return (
            f"inventory: '{s}' is not on the declared-ready list ({ready}); "
            "the Store kit for this vertical is unverified — domain depth not "
            "guaranteed"
        )
    return ""


def domain_gaps(
    vertical: str, capabilities: list
) -> list:
    """Capabilities that resolved to blocks but none domain-relevant.

    The vet-clinic failure shape: every capability marked REUSE, every
    reused block generic plumbing, zero domain content. A capability whose
    resolved blocks are disjoint from the vertical's kit domain set is
    recorded here — 'resolved to a block' must never read as 'resolved to
    the right block'.
    """
    kit = ready_kit(vertical)
    if not kit:
        return []
    domain = domain_blocks(kit, domain_kits())
    if not domain:
        return []
    gaps = []
    for cap in capabilities or []:
        blocks = set(cap.get("block_ids") or [])
        if not blocks or not blocks.isdisjoint(domain):
            continue
        gaps.append(
            {
                "capability_id": str(cap.get("capability_id") or cap.get("id") or "?"),
                "blocks": sorted(blocks),
                "note": (
                    f"resolved to generic blocks only — none from the "
                    f"{kit} kit domain set"
                ),
            }
        )
    return gaps
