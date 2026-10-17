"""Factory HTTP surface for Build Mode workbench (flag-gated)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.change_requests.queue import QUEUE_STATES, ChangeRequestQueue
from app.workbench.flags import build_mode_enabled, kimi_workbench_enabled
from app.workbench.session import (
    WorkbenchRejected,
    evaluate_gates,
    promote_request,
    run_workbench_session,
)

router = APIRouter(prefix="/v1/workbench", tags=["workbench"])


@router.get("/status")
async def workbench_status() -> Dict[str, Any]:
    return {
        "build_mode_enabled": build_mode_enabled(),
        "kimi_workbench_enabled": kimi_workbench_enabled(),
        "states": list(QUEUE_STATES),
        "trust_tier_ceiling": "staging/community",
        "deploy_credentials": False,
        "store_write": "impossible",
        "honesty": (
            "M4 workbench produces candidates and promotes via Factory packager only; "
            "flags default OFF; certified tier still requires chain-approve"
        ),
    }


class RunBody(BaseModel):
    request_id: str
    product_root: Optional[str] = None
    run_gates: bool = True


@router.post("/run")
async def run_session(body: RunBody) -> Dict[str, Any]:
    try:
        item = run_workbench_session(
            body.request_id,
            product_root=Path(body.product_root) if body.product_root else None,
            run_gates=body.run_gates,
        )
        return {"ok": True, "item": item}
    except WorkbenchRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


@router.post("/gates/{request_id}")
async def gates(request_id: str) -> Dict[str, Any]:
    try:
        item = evaluate_gates(request_id)
        return {"ok": True, "item": item}
    except WorkbenchRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


class PromoteBody(BaseModel):
    actor: str = Field(default="owner")


@router.post("/promote/{request_id}")
async def promote(request_id: str, body: PromoteBody | None = None) -> Dict[str, Any]:
    actor = (body.actor if body else "owner") or "owner"
    try:
        item = promote_request(request_id, actor=actor)
        return {"ok": True, "item": item}
    except WorkbenchRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc


@router.get("/queue")
async def list_queue(state: Optional[str] = None) -> Dict[str, Any]:
    if not build_mode_enabled():
        raise HTTPException(status_code=503, detail="BUILD_MODE_ENABLED is false")
    items = ChangeRequestQueue().list_items(state=state)
    return {"count": len(items), "items": items}


@router.get("/queue/{request_id}")
async def get_item(request_id: str) -> Dict[str, Any]:
    if not build_mode_enabled():
        raise HTTPException(status_code=503, detail="BUILD_MODE_ENABLED is false")
    item = ChangeRequestQueue().get(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item
