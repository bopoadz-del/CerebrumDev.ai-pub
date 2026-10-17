"""RAG activation/status contract for prebuilt domain RAG packs.

This module turns raw RAG pack metadata into a clean platform-facing contract
that tells callers whether a prebuilt RAG pack is available for a domain,
whether it can be attached, and what is required to use it.

The contract is intentionally metadata-only. No documents are ingested and no
embeddings are created here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.rag_pack_loader import get_rag_pack


def build_rag_activation_status(domain_id: str) -> Dict[str, Any]:
    """Return the activation/status contract for a domain's prebuilt RAG pack.

    Args:
        domain_id: The domain identifier (e.g. ``"legal"``).

    Returns:
        A dict describing availability, attachability, and requirements.
        If no RAG pack exists for the domain, returns a ``not_available``
        status without raising.
    """
    pack: Optional[Dict[str, Any]] = get_rag_pack(domain_id)

    if pack is None:
        return {
            "domain": domain_id,
            "status": "not_available",
            "attachable": False,
            "attached": False,
            "rag_pack": None,
            "requires_blocks": [],
            "recommended_with_blocks": [],
            "missing_requirements": [],
            "notes": ["No prebuilt RAG pack metadata found for this domain."],
        }

    source_policy = pack.get("source_policy") or {}
    return {
        "domain": domain_id,
        "status": "available_metadata_only",
        "attachable": True,
        "attached": False,
        "rag_pack": {
            "id": pack.get("id"),
            "collection_id": pack.get("collection_id"),
            "ingestion_status": pack.get("ingestion_status"),
            "fetch_mode": pack.get("fetch_mode"),
            "enterprise_specific": pack.get("enterprise_specific", False),
            "source_policy": {
                "requires_source_record": source_policy.get("requires_source_record", False),
                "requires_license_review": source_policy.get("requires_license_review", False),
                "requires_authority_rating": source_policy.get("requires_authority_rating", False),
            },
        },
        "requires_blocks": list(pack.get("requires_blocks", [])),
        "recommended_with_blocks": list(pack.get("recommended_with_blocks", [])),
        "missing_requirements": [],
        "notes": [
            "Prebuilt RAG pack metadata is available, but no documents or embeddings are ingested yet."
        ],
    }
