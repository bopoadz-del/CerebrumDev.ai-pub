"""Read-only loader for the Cerebrum-Blocks Prebuilt Domain RAG Pack shelf.

The factory discovers what prebuilt domain knowledge collections exist by
reading ``block_store/shelves/rag_packs.json`` from the resolved engine
checkout. RAG packs are pure metadata; no documents are ingested and no
embeddings are created here.

This module is intentionally read-only and does not change runtime behavior.
Wiring RAG packs into chain generation or retrieval is deferred to a follow-up
PR.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.engine_discovery import find_engine_root

logger = logging.getLogger(__name__)

_SHELF_REL_PATH = Path("block_store") / "shelves" / "rag_packs.json"


class RagPackLoaderError(Exception):
    """Raised when the RAG pack shelf cannot be located or parsed."""


def _shelf_path(engine_root: Optional[Path] = None) -> Path:
    """Return the absolute path to the RAG pack shelf file."""
    root = engine_root or find_engine_root()
    return root / _SHELF_REL_PATH


def load_rag_packs(engine_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load the raw RAG pack shelf JSON from the engine checkout.

    Args:
        engine_root: Optional explicit engine checkout path. If omitted,
            ``find_engine_root()`` is used.

    Raises:
        RagPackLoaderError: if the shelf is missing or not valid JSON.
    """
    path = _shelf_path(engine_root)
    if not path.exists():
        raise RagPackLoaderError(f"RAG pack shelf not found at {path}")

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise RagPackLoaderError(f"invalid JSON in RAG pack shelf {path}: {exc}")
    except OSError as exc:
        raise RagPackLoaderError(f"cannot read RAG pack shelf {path}: {exc}")


def _normalize_ingestion_status(value: Any) -> Dict[str, Any]:
    """Return a structured ingestion_status dict.

    Supports the legacy string value `"not_ingested"` for backward compatibility
    while preferring the new structured object shape.
    """
    if isinstance(value, dict):
        return {
            "state": value.get("state", "not_ingested"),
            "documents_total": value.get("documents_total", 0),
            "documents_indexed": value.get("documents_indexed", 0),
            "chunks_total": value.get("chunks_total", 0),
            "last_ingested_at": value.get("last_ingested_at"),
            "last_error": value.get("last_error"),
        }
    if value == "not_ingested":
        return {
            "state": "not_ingested",
            "documents_total": 0,
            "documents_indexed": 0,
            "chunks_total": 0,
            "last_ingested_at": None,
            "last_error": None,
        }
    return {
        "state": "not_ingested",
        "documents_total": 0,
        "documents_indexed": 0,
        "chunks_total": 0,
        "last_ingested_at": None,
        "last_error": None,
    }


def list_rag_packs(engine_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return a lightweight summary for each domain RAG pack.

    Each item contains: id, domain, name, description, collection_id,
    visibility, data_class, enterprise_specific, requires_blocks,
    recommended_with_blocks, source_types, expected_queries,
    expected_outputs, fetch_mode, source_policy, ingestion_status, and notes.
    """
    data = load_rag_packs(engine_root)
    packs = data.get("packs", [])
    return [
        {
            "id": p.get("id"),
            "domain": p.get("domain"),
            "name": p.get("name"),
            "description": p.get("description"),
            "collection_id": p.get("collection_id"),
            "visibility": p.get("visibility"),
            "data_class": p.get("data_class"),
            "enterprise_specific": p.get("enterprise_specific", False),
            "requires_blocks": p.get("requires_blocks", []),
            "recommended_with_blocks": p.get("recommended_with_blocks", []),
            "source_types": p.get("source_types", []),
            "expected_queries": p.get("expected_queries", []),
            "expected_outputs": p.get("expected_outputs", []),
            "fetch_mode": p.get("fetch_mode"),
            "source_policy": p.get("source_policy", {}),
            "ingestion_status": _normalize_ingestion_status(p.get("ingestion_status")),
            "notes": p.get("notes", []),
        }
        for p in packs
        if p.get("id")
    ]


def list_rag_pack_domain_ids(engine_root: Optional[Path] = None) -> List[str]:
    """Return the sorted list of RAG pack domain ids."""
    return sorted(p["domain"] for p in list_rag_packs(engine_root))


def get_rag_pack(
    domain_id: str, engine_root: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a single RAG pack by domain id."""
    for pack in list_rag_packs(engine_root):
        if pack["domain"] == domain_id:
            return pack
    return None
