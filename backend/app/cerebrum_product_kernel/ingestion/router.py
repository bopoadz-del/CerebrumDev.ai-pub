"""FastAPI routes for the Phase 2 client ingestion surface.

Mounted ONLY into generated products whose vertical is not in the
inventory exclusion set (medical, legal, veterinary, pharma) — a product
with no certified content to back must not receive a client-ingestion
surface. The mount is performed by the deploy main renderer.

Tenant resolution is the product's single path (app.tenancy.resolve_tenant
— §0.2): a missing, unknown, or client-named tenant is refused with 401 by
name, never mapped to a default. The Drive/formula handlers themselves land
in §2/§3; until then the routes refuse honestly with 501.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/v1/product/ingestion", tags=["ingestion-scaffold"])


def _require_product_tenant(request: Request) -> str:
    """Resolve the bound tenant from the authenticated principal.

    Reuses the product's single resolution path (§0.2): a missing token, an
    unbound token, or a client-supplied tenant identity is refused by name.
    """
    import app.tenancy as tenancy

    try:
        tenant = tenancy.resolve_tenant(request.headers)
    except tenancy.TenantRefused:
        raise HTTPException(status_code=401, detail="authentication_required")
    return tenant.tenant_id


@router.post("/drive/connect")
def drive_connect(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.get("/drive/callback")
def drive_callback(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.get("/drive/files")
def drive_files(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.post("/drive/sync/{file_id}")
def drive_sync(file_id: str, request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.post("/upload")
def upload_document(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.post("/formulas")
def submit_formula_overlay(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.get("/formulas")
def list_formula_overlays(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")
