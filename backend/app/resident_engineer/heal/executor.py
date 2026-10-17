"""L2 heal executor — allowlist + confirmation + pre/post + audit."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from app.cerebrum_product_kernel.audit import audit_record
from app.resident_engineer.flags import resident_engineer_enabled
from app.resident_engineer.heal.audit_log import HealAuditLog
from app.resident_engineer.heal.catalog import (
    ALLOWLISTED_HEAL_ACTIONS,
    build_heal_registry,
    get_heal_state,
    is_allowlisted,
)
from app.resident_engineer.heal.validate import (
    HealValidationError,
    default_post_check,
    default_pre_check,
    restore_state,
    snapshot_state,
)


class HealRejected(RuntimeError):
    """Allowlist / confirmation / flag rejection."""


async def execute_heal(
    action_id: str,
    *,
    confirmed: bool,
    tenant_id: str = "default",
    user_id: str = "system",
    principal_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    audit: Optional[HealAuditLog] = None,
    force_post_check_fail: bool = False,
    db_session: Any = None,
) -> Dict[str, Any]:
    """Run an allowlisted heal with deterministic validation + audit.

    ``force_post_check_fail`` is test-only to exercise rollback.
    """
    if not resident_engineer_enabled():
        raise HealRejected("RESIDENT_ENGINEER_ENABLED is false")
    if not is_allowlisted(action_id):
        raise HealRejected(f"action not allowlisted: {action_id}")
    if not confirmed:
        raise HealRejected("confirmation required for L2 heal")

    from app.resident_engineer.heal.approval import validate_heal_approval_or_raise

    effective_principal = principal_id or user_id
    try:
        validate_heal_approval_or_raise(
            db_session,
            approval_id=approval_id,
            tenant_id=tenant_id,
            principal_id=effective_principal,
            action_id=action_id,
        )
    except ValueError as exc:
        raise HealRejected(str(exc)) from exc

    registry = build_heal_registry()
    spec = registry.resolve(action_id)
    if spec is None or spec.handler is None:
        raise HealRejected(f"action not registered: {action_id}")

    audit_log = audit or HealAuditLog()
    request_id = str(uuid.uuid4())
    before = snapshot_state()

    ok_pre, pre_msg = default_pre_check(action_id, before)
    if not ok_pre:
        audit_log.append(
            audit_record(
                action_id=action_id,
                request_id=request_id,
                status="pre_check_failed",
                user_id=user_id,
                tenant_id=tenant_id,
                metadata={"message": pre_msg},
            )
        )
        raise HealValidationError(pre_msg)

    result = await spec.handler(  # type: ignore[misc]
        {"tenant_id": tenant_id, "user_id": user_id},
        arguments or {},
    )
    after = snapshot_state()

    ok_post, post_msg = default_post_check(action_id, before, after)
    if force_post_check_fail:
        ok_post, post_msg = False, "forced post-check failure"

    if not ok_post:
        restore_state(before)
        audit_log.append(
            audit_record(
                action_id=action_id,
                request_id=request_id,
                status="rolled_back",
                user_id=user_id,
                tenant_id=tenant_id,
                metadata={"message": post_msg, "before": before, "after": after},
            )
        )
        raise HealValidationError(f"post-check failed; rolled back: {post_msg}")

    record = audit_log.append(
        audit_record(
            action_id=action_id,
            request_id=request_id,
            status="simulated",
            user_id=user_id,
            tenant_id=tenant_id,
            metadata={
                "result": result,
                "allowlist": list(ALLOWLISTED_HEAL_ACTIONS),
                "executed": False,
                "simulation": True,
            },
        )
    )
    return {
        "ok": False,
        "executed": False,
        "simulation": True,
        "action_id": action_id,
        "result": result,
        "state": get_heal_state(),
        "audit": record,
        "detail": result.get("detail", "Simulated heal — no real execution performed"),
    }
