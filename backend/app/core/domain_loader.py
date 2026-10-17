import os
import httpx
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "http://localhost:8000")


def _normalize_status(container: Dict) -> str:
    """Derive a simple available / coming_soon status from container metadata."""
    if container.get("coming_soon"):
        return "coming_soon"
    if container.get("installable") or container.get("bundle_ready") or container.get("skeleton_ready"):
        return "available"
    return "coming_soon"


async def load_domain_manifest(domain_id: str) -> Optional[Dict]:
    """Load a single domain manifest from the Cerebrum store."""
    domains = await list_available_domains()
    for d in domains:
        if d["id"] == domain_id:
            return d
    return None


async def list_available_domains() -> List[Dict]:
    """Return all domains from the Cerebrum store."""
    url = f"{CEREBRUM_API_URL}/store/containers"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.error("Failed to fetch domains from Cerebrum store at %s: %s", url, exc)
        return []

    containers = data.get("containers", [])
    return [
        {
            "id": c.get("id"),
            "name": c.get("name") or c.get("id"),
            "status": _normalize_status(c),
            "description": c.get("description") or "",
        }
        for c in containers
        if c.get("id")
    ]
