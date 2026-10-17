"""Persisted heal approval validation for L2 Resident Engineer actions."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

_APPROVAL_TTL = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def heal_approval_digest(*, tenant_id: str, action_id: str, principal_id: str) -> str:
    payload = f"{tenant_id}:{action_id}:{principal_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_heal_approval(
    session: Session,
    *,
    tenant_id: str,
    principal_id: str,
    action_id: str,
    ttl: timedelta = _APPROVAL_TTL,
) -> Dict[str, Any]:
    """Create a pending heal approval record."""
    from app.steward.audit_models import HealApproval

    approval_id = str(uuid.uuid4())
    digest = heal_approval_digest(
        tenant_id=tenant_id,
        action_id=action_id,
        principal_id=principal_id,
    )
    row = HealApproval(
        id=approval_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        action_id=action_id,
        digest=digest,
        expires_at=_utcnow() + ttl,
    )
    session.add(row)
    session.flush()
    return {
        "approval_id": approval_id,
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "action_id": action_id,
        "digest": digest,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def consume_heal_approval(
    session: Session,
    *,
    approval_id: str,
    tenant_id: str,
    principal_id: str,
    action_id: str,
) -> None:
    """Validate and consume a heal approval — raises ValueError on failure."""
    from app.steward.audit_models import HealApproval

    row = session.execute(
        select(HealApproval).where(HealApproval.id == approval_id)
    ).scalar_one_or_none()
    if row is None:
        raise ValueError("heal approval not found")
    if row.tenant_id != tenant_id:
        raise ValueError("heal approval tenant mismatch")
    if row.principal_id != principal_id:
        raise ValueError("heal approval principal mismatch")
    if row.action_id != action_id:
        raise ValueError("heal approval action mismatch")
    if row.consumed_at is not None:
        raise ValueError("heal approval already consumed")
    if row.expires_at is not None and row.expires_at <= _utcnow():
        raise ValueError("heal approval expired")
    expected = heal_approval_digest(
        tenant_id=tenant_id,
        action_id=action_id,
        principal_id=principal_id,
    )
    if row.digest != expected:
        raise ValueError("heal approval digest mismatch")
    row.consumed_at = _utcnow()
    session.flush()


def validate_heal_approval_or_raise(
    session: Optional[Session],
    *,
    approval_id: Optional[str],
    tenant_id: str,
    principal_id: str,
    action_id: str,
) -> None:
    """Require a valid persisted approval when a database session is available."""
    if session is None:
        if not approval_id:
            raise ValueError("heal approval_id required")
        return
    if not approval_id:
        raise ValueError("heal approval_id required")
    consume_heal_approval(
        session,
        approval_id=approval_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        action_id=action_id,
    )
