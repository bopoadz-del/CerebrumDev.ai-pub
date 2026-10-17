"""Live audit artifact validation for workbench promotion.

A workbench candidate can only be promoted if it carries a fresh, all-LIVE
post-deploy smoke artifact. Stale or dead-check artifacts hard-fail promotion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import json


class AuditRejected(ValueError):
    """Audit artifact is missing, malformed, stale, or contains DEAD checks."""


def validate_audit_artifact(path: Path, *, now: datetime | None = None) -> Dict[str, Any]:
    """Validate a live audit artifact JSON file.

    Rules:
      - file must exist and parse as JSON
      - must contain a non-empty "checks" list
      - every check status == "LIVE"
      - "ran_at" must parse as ISO-8601
      - age <= 24h

    Returns the parsed artifact dict on success.
    Raises AuditRejected on any failure.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if not path.is_file():
        raise AuditRejected(f"audit artifact not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuditRejected(f"audit artifact is not valid JSON: {exc}") from exc

    checks = data.get("checks") or []
    if not checks:
        raise AuditRejected("audit artifact has no checks")

    dead = [c.get("name", f"check-{i}") for i, c in enumerate(checks) if c.get("status") != "LIVE"]
    if dead:
        raise AuditRejected(f"audit artifact contains DEAD checks: {dead}")

    ran_at_raw = data.get("ran_at")
    if not ran_at_raw:
        raise AuditRejected("audit artifact missing ran_at")
    try:
        ran_at = datetime.fromisoformat(str(ran_at_raw))
    except ValueError as exc:
        raise AuditRejected(f"audit artifact ran_at is not ISO-8601: {exc}") from exc

    if ran_at.tzinfo is None:
        ran_at = ran_at.replace(tzinfo=timezone.utc)

    if now - ran_at > timedelta(hours=24):
        raise AuditRejected(f"audit artifact is stale (ran_at={ran_at.isoformat()})")

    return data
