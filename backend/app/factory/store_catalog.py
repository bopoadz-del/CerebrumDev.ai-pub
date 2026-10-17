"""What the Store actually has, in terms a conversation can use.

The architect is shown bare block ids. That is enough to bind blocks, but
not enough to talk to a customer: the Floor chat has to be able to say
"we have a ready-made notification connector", "there is a Google Drive
part in the store but it is not cleared for factory builds yet", or
"there is no Procore connector in the store at all".

Everything here is read from the Store's own manifests (``tags``,
``description``) and the factory's kit shelf -- nothing is a hardcoded
list of names, so a connector published and shelved tomorrow is offered
tomorrow.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Manifest tags that mean "this block talks to a system outside the platform".
CONNECTOR_TAGS = frozenset({"integration", "connector", "connector-infra"})
MCP_TAGS = frozenset({"mcp"})
_DESCRIPTION_CHARS = 110

#: The base every build stands on. It is on the kit shelf, but it is not
#: domain depth -- offering it as "we have a kit for that" would be the
#: exact overclaim the kit notice exists to prevent.
BASE_KITS = frozenset({"platform"})

#: The registry walk shells out to git once per block directory (tracked-only
#: determinism), which is seconds on a 100+ block Store -- and the chat reads
#: the catalog on every turn. The Store only changes on a deploy or a clone
#: refresh, so a short TTL costs nothing in freshness.
_CACHE_TTL_S = 300.0
_cache: Dict[str, Any] = {}


def clear_cache() -> None:
    _cache.clear()


def _manifest(block_id: str, blocks_root: Path) -> Dict[str, Any]:
    for candidate in (blocks_root / "block_registry" / block_id / "block.json",):
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            logger.warning("store catalog: unreadable manifest %s", candidate)
    return {}


def _entry(block_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    description = " ".join(str(manifest.get("description") or "").split())
    return {
        "id": block_id,
        "name": str(manifest.get("name") or block_id),
        "description": description[:_DESCRIPTION_CHARS],
        "tags": [str(t).lower() for t in (manifest.get("tags") or [])],
    }


def store_catalog(blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Cached :func:`build_store_catalog` (see ``_CACHE_TTL_S``)."""
    key = str(blocks_root or "")
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    catalog = build_store_catalog(blocks_root)
    _cache[key] = (now, catalog)
    return catalog


def build_store_catalog(blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Blocks, connectors, MCP parts and kits the factory can offer.

    ``connectors`` / ``mcp`` hold dual-registered parts only: a block the
    factory cannot bind is not something the chat may attach. Parts that are
    published to the Store but not on the factory shelf are listed under
    ``not_cleared`` -- the chat must not attach them, and must not pretend
    they do not exist either.
    """
    from app.factory.dual_registry import (
        _default_blocks_root,
        load_blocks_registry,
        load_factory_shelf,
    )
    from app.factory.kit_pack import load_shelf_kit_map

    root = Path(blocks_root) if blocks_root else _default_blocks_root()
    # One registry walk, not two: dual-registered is shelf AND store.
    registry = set(load_blocks_registry(blocks_root))
    ids = sorted(set(load_factory_shelf()) & registry)
    connectors: List[Dict[str, Any]] = []
    mcp: List[Dict[str, Any]] = []
    for bid in ids:
        entry = _entry(bid, _manifest(bid, root))
        tags = set(entry["tags"])
        if tags & MCP_TAGS:
            mcp.append(entry)
        elif tags & CONNECTOR_TAGS:
            connectors.append(entry)

    # Everything the Store holds that the Factory refuses -- not only the
    # connector-tagged ones. That filter made sense when clearance was a
    # 25-entry allow-list and the remainder was most of the Store; now the
    # shelf resolves live and the remainder IS the refusal list, so naming
    # only part of it would hide the rest from the chat.
    not_cleared: List[Dict[str, Any]] = [
        _entry(bid, _manifest(bid, root)) for bid in sorted(registry - set(ids))
    ]

    try:
        kits = sorted(set(load_shelf_kit_map().values()) - BASE_KITS)
    except Exception:  # noqa: BLE001
        logger.warning("store catalog: kit shelf unreadable", exc_info=True)
        kits = []
    # What the Store holds, beside what the Factory has cleared. The chat was
    # told there was one kit while the Store shelved nineteen: a finance build
    # heard "no ready kit" with kits/finance_ops sitting there marked
    # available. Reported, never claimed as attachable -- kit files are still
    # vendored from the Factory's own kits/.
    from app.factory.dual_registry import shelf_from_store
    from app.factory.kit_pack import kit_map_from_store

    try:
        store_blocks = sorted(shelf_from_store(blocks_root))
        store_kits = sorted(set(kit_map_from_store(blocks_root).values()))
    except Exception:  # noqa: BLE001 -- inventory is context, never a blocker
        logger.warning("store catalog: store inventory unreadable", exc_info=True)
        store_blocks, store_kits = [], []

    # Which of those blocks anyone has actually vouched for. Every block.json
    # in the Store says trust_tier "platform", so the tier separates nothing;
    # the evidence is the Store's certification record, and a product that
    # labels a layer "certified" has to be able to point at it.
    from app.factory.dual_registry import certified_ids

    try:
        certified = sorted(certified_ids(blocks_root) & registry)
    except Exception:  # noqa: BLE001 -- no evidence is context, never a blocker
        logger.warning("store catalog: certification record unreadable", exc_info=True)
        certified = []

    return {
        "blocks": ids,
        "connectors": connectors,
        "mcp": mcp,
        "not_cleared": not_cleared,
        "kits": kits,
        "store_blocks": store_blocks,
        "store_kits": store_kits,
        "certified": certified,
    }


def offerable_connector_ids(catalog: Dict[str, Any]) -> List[str]:
    """Ids the chat may attach: shelved third-party connectors and MCP parts."""
    return sorted(
        {e["id"] for e in catalog.get("connectors") or []}
        | {e["id"] for e in catalog.get("mcp") or []}
    )


def _lines(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return "  (none today)"
    return "\n".join(
        f"  - {e['id']}" + (f": {e['description']}" if e["description"] else "")
        for e in entries
    )


def render_for_chat(catalog: Dict[str, Any]) -> str:
    """The catalog as the session-facts text the Floor chat model reads."""
    return "\n".join(
        [
            "STORE INVENTORY (ready-made blocks): "
            + (", ".join(catalog.get("blocks") or []) or "(empty)")
            + ".",
            "CONNECTORS to third-party systems, ready-made and attachable:",
            _lines(catalog.get("connectors") or []),
            "MCP parts, ready-made and attachable:",
            _lines(catalog.get("mcp") or []),
            "IN THE STORE BUT NOT CLEARED FOR FACTORY BUILDS (cannot be attached):",
            _lines(catalog.get("not_cleared") or []),
            # The one line here that is evidence rather than inventory. Prefer
            # these when a capability can be served either way, and never call
            # a layer certified on the strength of a block that is not listed.
            "CERTIFIED (survived the Store's control-delete: gut the entry "
            "method and the tests go red): "
            + (", ".join(catalog.get("certified") or []) or "(none yet)")
            + ". Every other block is unproven, not unusable.",
            "KITS (deep, certified domain packs): "
            + (", ".join(catalog.get("kits") or []) or "(none)")
            + ".",
        ]
    )
