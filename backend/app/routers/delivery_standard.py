"""Delivery-standard API — the canonical brief for the factory's coder (Kimi Code).

GET  /v1/factory/delivery-standard         → standard hash + required keys
POST /v1/factory/delivery-standard/render  → assembled brief (fail-closed)
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.auth import Principal, require_api_key
from ..factory import delivery_standard

router = APIRouter()


class RenderBody(BaseModel):
    platform: Dict[str, Any] = Field(default_factory=dict)
    domain_pack: Dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def standard_info(principal: Principal = Depends(require_api_key)):
    return {
        "ok": True,
        "sha256": delivery_standard.standard_sha256(),
        "platform_keys": list(delivery_standard.REQUIRED_PLATFORM_KEYS),
        "domain_pack_fields": list(delivery_standard.REQUIRED_DOMAIN_KEYS),
        "coder": "kimi_code",
    }


@router.post("/render")
async def render_brief(
    body: RenderBody, principal: Principal = Depends(require_api_key)
):
    try:
        brief = delivery_standard.render(body.platform, body.domain_pack)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "brief": brief,
        "sha256": delivery_standard.standard_sha256(),
    }
