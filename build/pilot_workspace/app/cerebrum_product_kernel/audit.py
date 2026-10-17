"""Minimal audit record helper (neutralized from RetailClient audit patterns)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def audit_record(
    *,
    action_id: str,
    request_id: str,
    status: str,
    user_id: str,
    tenant_id: str,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "action_id": action_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
        "request_id": request_id,
        "status": status,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
