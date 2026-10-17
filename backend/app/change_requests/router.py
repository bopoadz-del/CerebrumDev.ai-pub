"""Factory HTTP surface for change-request intake (flag-gated)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.change_requests.flags import change_request_intake_enabled
from app.change_requests.intake import IntakeRejected, intake_change_request
from app.change_requests.queue import ChangeRequestQueue
from app.change_requests.store_compare import compare_lockfile_to_store, compare_product_dna_dir

router = APIRouter(prefix="/v1/change-requests", tags=["change-requests"])


@router.get("/status")
async def change_request_status() -> Dict[str, Any]:
    return {
        "intake_enabled": change_request_intake_enabled(),
        "states": [
            "received",
            "validated",
            "dry_run_evaluated",
            "awaiting_approval",
            "approved",
            "rejected",
            "workbench_running",
            "candidate_ready",
            "gate_passed",
            "gate_failed",
            "staged",
        ],
        "execution": False,
        "honesty": "M3 paperwork only — approval does not run agents; M4 workbench executes under BUILD_MODE_ENABLED",
    }


@router.post("/intake")
async def intake(document: Dict[str, Any]) -> Dict[str, Any]:
    try:
        item = intake_change_request(document)
        return {"ok": True, "item": item}
    except IntakeRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


@router.get("/queue")
async def list_queue(state: Optional[str] = None) -> Dict[str, Any]:
    if not change_request_intake_enabled():
        raise HTTPException(status_code=503, detail="CHANGE_REQUEST_INTAKE_ENABLED is false")
    items = ChangeRequestQueue().list_items(state=state)
    return {"count": len(items), "items": items}


@router.get("/queue/{request_id}")
async def get_item(request_id: str) -> Dict[str, Any]:
    if not change_request_intake_enabled():
        raise HTTPException(status_code=503, detail="CHANGE_REQUEST_INTAKE_ENABLED is false")
    item = ChangeRequestQueue().get(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item


class DecisionBody(BaseModel):
    approved: bool
    actor: str = "owner"
    note: str = ""


@router.post("/queue/{request_id}/decision")
async def record_decision(request_id: str, body: DecisionBody) -> Dict[str, Any]:
    """Record approved/rejected — does NOT execute (M4)."""
    if not change_request_intake_enabled():
        raise HTTPException(status_code=503, detail="CHANGE_REQUEST_INTAKE_ENABLED is false")
    try:
        item = ChangeRequestQueue().record_decision(
            request_id, approved=body.approved, actor=body.actor, note=body.note
        )
        return {"ok": True, "item": item}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc


class StoreCompareBody(BaseModel):
    product_root: Optional[str] = None
    lockfile: Optional[Dict[str, Any]] = None
    store_versions: Optional[Dict[str, str]] = None
    changelogs: Optional[Dict[str, str]] = None


@router.post("/store-compare")
async def store_compare(body: StoreCompareBody) -> Dict[str, Any]:
    """Read-only advisory: pinned DNA blocks vs Store/shelf versions."""
    if body.product_root:
        return compare_product_dna_dir(
            Path(body.product_root),
            store_versions=body.store_versions,
            changelogs=body.changelogs,
        )
    if body.lockfile is not None:
        return compare_lockfile_to_store(
            body.lockfile,
            store_versions=body.store_versions,
            changelogs=body.changelogs,
        )
    raise HTTPException(status_code=400, detail="provide product_root or lockfile")
