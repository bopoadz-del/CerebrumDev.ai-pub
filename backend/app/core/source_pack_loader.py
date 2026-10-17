"""Read-only loader for the Cerebrum-Blocks Domain Source Pack shelf.

The factory discovers how each domain should think, respond, and wire blocks by
reading ``block_store/shelves/source_packs.json`` from the resolved engine
checkout. Source packs are pure metadata; no documents are ingested here.

This module is intentionally read-only and does not change runtime behavior.
Wiring source packs into chain generation is deferred to a follow-up PR.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.engine_discovery import find_engine_root

logger = logging.getLogger(__name__)

_SHELF_REL_PATH = Path("block_store") / "shelves" / "source_packs.json"


class SourcePackLoaderError(Exception):
    """Raised when the source pack shelf cannot be located or parsed."""


def _shelf_path(engine_root: Optional[Path] = None) -> Path:
    """Return the absolute path to the source pack shelf file."""
    root = engine_root or find_engine_root()
    return root / _SHELF_REL_PATH


def load_source_packs(engine_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load the raw source pack shelf JSON from the engine checkout.

    Args:
        engine_root: Optional explicit engine checkout path. If omitted,
            ``find_engine_root()`` is used.

    Raises:
        SourcePackLoaderError: if the shelf is missing or not valid JSON.
    """
    path = _shelf_path(engine_root)
    if not path.exists():
        raise SourcePackLoaderError(f"source pack shelf not found at {path}")

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SourcePackLoaderError(f"invalid JSON in source pack shelf {path}: {exc}")
    except OSError as exc:
        raise SourcePackLoaderError(f"cannot read source pack shelf {path}: {exc}")


def list_source_packs(engine_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return a lightweight summary for each domain source pack.

    Each item contains: id, name, domain, description, expert_prompt, workflow,
    use_cases, example_prompts, expected_inputs, expected_outputs, blocks.
    """
    data = load_source_packs(engine_root)
    packs = data.get("packs", [])
    return [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "domain": p.get("domain"),
            "description": p.get("description"),
            "expert_prompt": p.get("expert_prompt"),
            "workflow": p.get("workflow"),
            "use_cases": p.get("use_cases", []),
            "example_prompts": p.get("example_prompts", []),
            "expected_inputs": p.get("expected_inputs", []),
            "expected_outputs": p.get("expected_outputs", []),
            "blocks": p.get("blocks", []),
        }
        for p in packs
        if p.get("id")
    ]


def list_source_pack_domain_ids(engine_root: Optional[Path] = None) -> List[str]:
    """Return the sorted list of source pack domain ids."""
    return sorted(p["id"] for p in list_source_packs(engine_root))


def get_source_pack(
    domain_id: str, engine_root: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a single source pack by domain id."""
    for pack in list_source_packs(engine_root):
        if pack["id"] == domain_id:
            return pack
    return None
