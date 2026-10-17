"""Factory-side Resident Engineer routes (feature-flagged, default off)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.resident_engineer.diagnosis import build_failure_report
from app.resident_engineer.flags import resident_engineer_enabled
from app.resident_engineer.heal.approval import create_heal_approval
from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS
from app.resident_engineer.heal.executor import HealRejected, execute_heal
from app.resident_engineer.heal.validate import HealValidationError
from app.resident_engineer.modes import AutonomyLevel, draft_change_request
from app.resident_engineer.observe import observe

try:
    from app.steward.auth import AuthenticatedPrincipal, get_authenticated_principal
    from app.steward.db import init_engine, session_scope

    _STEWARD_AUTH = True
except ImportError:  # pragma: no cover - non-estate products
    _STEWARD_AUTH = False

router = APIRouter(prefix="/v1/resident", tags=["resident-engineer"])


def _require_enabled() -> None:
    if not resident_engineer_enabled():
        raise HTTPException(
            status_code=503,
            detail="RESIDENT_ENGINEER_ENABLED is false (default)",
        )


if _STEWARD_AUTH:

    async def _resolve_principal(
        principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        return principal

else:

    async def _resolve_principal() -> None:
        return None


@router.get("/status")
async def resident_status() -> Dict[str, Any]:
    """Always available — reports flag + allowlist (no heal execution)."""
    return {
        "enabled": resident_engineer_enabled(),
        "mode": "resident",
        "maturity": "APPRENTICE",
        "allowlisted_heal_actions": list(ALLOWLISTED_HEAL_ACTIONS),
        "auth_required": _STEWARD_AUTH,
        "levels": {
            AutonomyLevel.L1.value: "observe",
            AutonomyLevel.L2.value: "allowlisted_heal",
            "L3_L5": "draft_change_request_only",
        },
    }


@router.get("/observe")
async def resident_observe(
    log_text: str = "",
    principal: Optional[Any] = Depends(_resolve_principal),
) -> Dict[str, Any]:
    _require_enabled()
    if _STEWARD_AUTH and principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    root = Path(__file__).resolve().parents[2]
    try:
        return observe(root, log_text=log_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="observe failed") from exc


class DiagnoseBody(BaseModel):
    symptom: str = ""
    related_action_ids: list[str] = Field(default_factory=list)


@router.post("/diagnose")
async def resident_diagnose(
    body: DiagnoseBody,
    principal: Optional[Any] = Depends(_resolve_principal),
) -> Dict[str, Any]:
    _require_enabled()
    if _STEWARD_AUTH and principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    root = Path(__file__).resolve().parents[2]
    return build_failure_report(
        product_root=root,
        symptom=body.symptom,
        related_action_ids=body.related_action_ids or None,
    )


class HealApprovalBody(BaseModel):
    action_id: str


@router.post("/heal/approval")
async def resident_heal_approval(
    body: HealApprovalBody,
    principal: Optional[Any] = Depends(_resolve_principal),
) -> Dict[str, Any]:
    _require_enabled()
    if not _STEWARD_AUTH or principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if body.action_id not in ALLOWLISTED_HEAL_ACTIONS:
        raise HTTPException(status_code=403, detail="action not allowlisted")
    init_engine()
    with session_scope() as session:
        approval = create_heal_approval(
            session,
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            action_id=body.action_id,
        )
    return {"ok": True, **approval}


class HealBody(BaseModel):
    action_id: str
    confirmed: bool = False
    approval_id: Optional[str] = None


class DraftBody(BaseModel):
    level: str = AutonomyLevel.L3.value
    kind: str = "ExpansionRequest"
    summary: str = ""
    product_id: Optional[str] = None


@router.post("/draft-change-request")
async def resident_draft(
    body: DraftBody,
    principal: Optional[Any] = Depends(_resolve_principal),
) -> Dict[str, Any]:
    _require_enabled()
    if not _STEWARD_AUTH or principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        return draft_change_request(
            level=body.level,  # type: ignore[arg-type]
            kind=body.kind,
            summary=body.summary,
            product_id=body.product_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class EscalateBody(BaseModel):
    product_id: str
    dna_version: str = "1.0.0"
    symptom: str
    target: str = "runtime"
    failed_action_id: Optional[str] = None
    submit_to_intake: bool = True


@router.post("/change-request")
async def resident_emit_change_request(
    body: EscalateBody,
    principal: Optional[Any] = Depends(_resolve_principal),
) -> Dict[str, Any]:
    """Emit a signed REPAIR change-request (flag-gated; M3 paperwork).

    Used when L2 heals dead-end and Resident escalates to Factory intake.
    """
    _require_enabled()
    if not _STEWARD_AUTH or principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    from app.change_requests.emit import EmitRejected, emit_repair_from_escalation

    try:
        return emit_repair_from_escalation(
            product_id=body.product_id,
            dna_version=body.dna_version,
            symptom=body.symptom,
            target=body.target,
            failed_action_id=body.failed_action_id,
            submit_to_intake=body.submit_to_intake,
        )
    except EmitRejected as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/heal")
async def resident_heal(
    body: HealBody,
    principal: Optional[Any] = Depends(_resolve_principal),
) -> Dict[str, Any]:
    _require_enabled()
    # Heal EXECUTES allowlisted repair actions — it must fail closed. Require the
    # steward auth module AND an authenticated principal; never run as a default
    # "operator" when auth is unavailable.
    if not _STEWARD_AUTH or principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    tenant_id = principal.tenant_id
    user_id = principal.principal_id
    principal_id = principal.principal_id
    try:
        init_engine()
        with session_scope() as session:
            return await execute_heal(
                body.action_id,
                confirmed=body.confirmed,
                tenant_id=tenant_id,
                user_id=user_id,
                principal_id=principal_id,
                approval_id=body.approval_id,
                db_session=session,
            )
    except HealRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HealValidationError as exc:
        escalation = None
        try:
            from app.change_requests.emit import emit_repair_from_escalation
            from app.change_requests.flags import resident_emit_change_requests_enabled

            if resident_emit_change_requests_enabled():
                escalation = emit_repair_from_escalation(
                    product_id=tenant_id or "unknown-product",
                    dna_version="1.0.0",
                    symptom=str(exc),
                    failed_action_id=body.action_id,
                    submit_to_intake=True,
                )
        except Exception:  # noqa: BLE001 — escalation must not mask heal error
            escalation = None
        detail: Dict[str, Any] = {"error": str(exc)}
        if escalation:
            detail["escalation"] = escalation
        raise HTTPException(status_code=409, detail=detail) from exc
