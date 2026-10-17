"""Read-only loader for the Cerebrum-Blocks Domain Virgin Edition shelf.

The factory discovers what domain editions the store offers by reading
``block_store/shelves/virgin_domains.json`` from the resolved engine checkout
instead of maintaining a hand-edited list.

This module is intentionally read-only and does not change runtime behavior.
Wiring the shelf into chain generation / block taxonomy is deferred to a
follow-up PR.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.engine_discovery import find_engine_root

logger = logging.getLogger(__name__)

_SHELF_REL_PATH = Path("block_store") / "shelves" / "virgin_domains.json"


class VirginShelfLoaderError(Exception):
    """Raised when the virgin shelf cannot be located or parsed."""


def _shelf_path(engine_root: Optional[Path] = None) -> Path:
    """Return the absolute path to the virgin domains shelf file."""
    root = engine_root or find_engine_root()
    return root / _SHELF_REL_PATH


def load_virgin_shelf(engine_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load the raw virgin shelf JSON from the engine checkout.

    Args:
        engine_root: Optional explicit engine checkout path. If omitted,
            ``find_engine_root()`` is used.

    Raises:
        VirginShelfLoaderError: if the shelf is missing or not valid JSON.
    """
    path = _shelf_path(engine_root)
    if not path.exists():
        raise VirginShelfLoaderError(f"virgin shelf not found at {path}")

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise VirginShelfLoaderError(f"invalid JSON in virgin shelf {path}: {exc}")
    except OSError as exc:
        raise VirginShelfLoaderError(f"cannot read virgin shelf {path}: {exc}")


def list_virgin_domains(engine_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return a lightweight summary for each virgin domain edition.

    Each item contains: id, name, domain, version, description, container_class,
    blocks.
    """
    data = load_virgin_shelf(engine_root)
    editions = data.get("editions", [])
    return [
        {
            "id": ed.get("id"),
            "name": ed.get("name"),
            "domain": ed.get("domain"),
            "version": ed.get("version"),
            "description": ed.get("description"),
            "container_class": ed.get("container_class"),
            "blocks": ed.get("blocks", []),
        }
        for ed in editions
        if ed.get("id")
    ]


def get_virgin_domain(
    domain_id: str, engine_root: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a single virgin domain edition by id."""
    for domain in list_virgin_domains(engine_root):
        if domain["id"] == domain_id:
            return domain
    return None


def virgin_domain_ids(engine_root: Optional[Path] = None) -> List[str]:
    """Return the sorted list of virgin domain ids."""
    return sorted(d["id"] for d in list_virgin_domains(engine_root))


def is_virgin_domain(domain_id: str, engine_root: Optional[Path] = None) -> bool:
    """Return True if *domain_id* appears in the virgin shelf."""
    return domain_id in virgin_domain_ids(engine_root)
