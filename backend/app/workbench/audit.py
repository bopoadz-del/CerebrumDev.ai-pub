"""Append-only audit helpers for workbench sessions on the change-request record."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit_event(
    kind: str,
    *,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "summary": summary,
        "details": details or {},
        "at": _now(),
        "immutable": True,
    }
