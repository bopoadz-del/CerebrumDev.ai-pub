import os
import httpx
import logging
from typing import List, Dict
from fastapi import HTTPException

logger = logging.getLogger(__name__)

CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "http://localhost:8000")
CEREBRUM_API_KEY = os.getenv("CEREBRUM_API_KEY")


async def fetch_block_registry() -> Dict[str, Dict]:
    """Fetch the block registry from the Cerebrum API.

    Returns a mapping of block name -> block metadata.
    Raises HTTPException(503) if the store is unreachable.
    """
    url = f"{CEREBRUM_API_URL}/v1/blocks"
    headers = {}
    if CEREBRUM_API_KEY:
        headers["Authorization"] = f"Bearer {CEREBRUM_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.error("Failed to fetch block registry from %s: %s", url, exc)
        raise HTTPException(status_code=503, detail=f"Cerebrum block store unreachable: {exc}")

    registry = {}
    for block in data.get("blocks", []):
        name = block.get("name")
        if not name or block.get("status") == "failed":
            continue
        registry[name] = block
    return registry


async def map_features(feature_ids: List[str]) -> List[str]:
    """Return valid block names for the given feature IDs."""
    registry = await fetch_block_registry()
    return [f for f in feature_ids if f in registry]
