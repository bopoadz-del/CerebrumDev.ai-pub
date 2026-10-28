"""FastAPI routes for the Phase 2 client ingestion surface.

Mounted ONLY into generated products whose vertical is not in the
inventory exclusion set (medical, legal, veterinary, pharma) — a product
with no certified content to back must not receive a client-ingestion
surface. The mount is performed by the deploy main renderer.

Tenant resolution is the product's single path (app.tenancy.resolve_tenant
— §0.2): a missing, unknown, or client-named tenant is refused with 401 by
name, never mapped to a default.

The ``/upload`` path is REAL as of F-1a: it validates (415/413/400), stores the
blob in the tenant-bound object store, and creates a genuinely-queued parse job
for the F-1b parser. The Drive/formula handlers land in §2/§3 and until then
refuse honestly with 501 — only the upload path is implemented.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile

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
async def upload_document(
    request: Request,
    files: Optional[List[UploadFile]] = File(None),
) -> dict:
    """Accept the client's ``+`` attach: validate, store, queue a parse job.

    Tenant is resolved FIRST (401 for an unbound caller) — before any file
    work — so a missing-auth request answers 401, not a body-parse error. One
    file returns a flat ``{object_id, job_id, status, source_name, bytes}``;
    several return ``{items: [...], count}``.
    """
    tenant_id = _require_product_tenant(request)
    return await ingest_uploads(tenant_id, files)


@router.get("/upload/{job_id}")
def upload_status(job_id: str, request: Request, response: Response) -> dict:
    """The real parse-job status for ``job_id`` under the resolved tenant."""
    tenant_id = _require_product_tenant(request)
    response.headers["Cache-Control"] = "no-store"
    return job_status_payload(tenant_id, job_id)


@router.post("/formulas")
def submit_formula_overlay(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@router.get("/formulas")
def list_formula_overlays(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


# ── shared ingest helpers (reused by the Factory-floor session route) ───────
#
# One validate -> store -> queue pipeline for BOTH surfaces (the generated
# product's /v1/product/ingestion/upload and the pilot's Floor upload). The
# caller resolves the tenant id and passes it in; these never read headers.


async def ingest_uploads(
    tenant_id: str, files: Optional[List["UploadFile"]]
) -> dict:
    """Validate, store, and queue a parse job for each uploaded file.

    Returns the flat single-file shape for one file, or ``{items, count}`` for
    several. 400 when no file was sent (mirrors ``app.routers.upload``).
    """
    from .object_store import save_object
    from .parse_job import ParseStatus, create_job
    from .upload_validation import validate_and_read

    uploads = [f for f in (files or []) if f is not None]
    if not uploads:
        raise HTTPException(status_code=400, detail="No files provided")

    # Validate and read EVERY file before storing ANY. A later file that fails
    # (415/413/400) must not leave an earlier file already stored with a queued
    # job, so all validation happens in this first pass and raises before the
    # first save_object.
    validated: List[tuple] = []
    for file in uploads:
        safe_name, data = await validate_and_read(file)
        validated.append((safe_name, data))

    items = []
    for safe_name, data in validated:
        stored = save_object(tenant_id, safe_name, data)
        job = create_job(
            tenant_id,
            object_id=stored.object_id,
            source_name=stored.source_name,
            status=ParseStatus.QUEUED,
        )
        items.append(
            {
                "object_id": stored.object_id,
                "job_id": job.job_id,
                "status": job.status.value,
                "source_name": stored.source_name,
                "bytes": stored.size_bytes,
            }
        )
    if len(items) == 1:
        return items[0]
    return {"items": items, "count": len(items)}


def job_status_payload(tenant_id: str, job_id: str) -> dict:
    """The real parse-job record for ``job_id``, or 404 if the tenant has none."""
    from .parse_job import get_job

    job = get_job(tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="parse job not found")
    return {
        "job_id": job.job_id,
        "object_id": job.object_id,
        "source_name": job.source_name,
        "status": job.status.value,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "last_error": job.last_error,
        "pages": job.pages,
        "sheets": job.sheets,
        "rows": job.rows,
    }
