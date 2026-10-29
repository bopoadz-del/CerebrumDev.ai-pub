"""FastAPI routes for the Phase 2 client ingestion surface.

Mounted ONLY into generated products whose vertical is not in the
inventory exclusion set (medical, legal, veterinary, pharma) — a product
with no certified content to back must not receive a client-ingestion
surface. The mount is performed by the deploy main renderer.

Tenant resolution is the product's single path (app.tenancy.resolve_tenant
— §0.2): a missing, unknown, or client-named tenant is refused with 401 by
name, never mapped to a default.

The ``/upload`` path is REAL: it validates (415/413/400), stores the blob in the
tenant-bound object store, and parses each stored object inline and by default
(the F-1b parser runs on upload), so the response status reflects the real
post-parse state (``parsed`` / ``failed``). The Drive handlers land in
§2/§3 and until then refuse honestly with 501 — only the upload path is
implemented.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

# The REAL product-facing routes (upload, status, answer). This is the ONLY
# router the deploy renderer mounts into a GENERATED product — the 501
# drive placeholders live on ``_scaffold_router`` below and are NOT
# shipped into products (F-1d §2: no 501 route ships in a generated product).
product_router = APIRouter(prefix="/v1/product/ingestion", tags=["ingestion"])

# The out-of-scope Drive placeholders. They still refuse honestly with
# 501 on the Factory host (where the combined ``router`` is used) and in the
# full-router tests, but the product renderer never mounts them.
_scaffold_router = APIRouter(
    prefix="/v1/product/ingestion", tags=["ingestion-scaffold"]
)


class AnswerRequest(BaseModel):
    """A product/Floor question. ``answer_text`` is the model's prose from the
    host's single LLM egress; on a kernel-covered query the kernel ignores it
    and computes the number itself, so a wrong number in the prose can never
    reach the client."""

    query: str = Field(..., min_length=1)
    answer_text: str = ""


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


@_scaffold_router.post("/drive/connect")
def drive_connect(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@_scaffold_router.get("/drive/callback")
def drive_callback(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@_scaffold_router.get("/drive/files")
def drive_files(request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@_scaffold_router.post("/drive/sync/{file_id}")
def drive_sync(file_id: str, request: Request) -> None:
    _require_product_tenant(request)
    raise HTTPException(status_code=501, detail="phase2_ingestion_scaffold_not_implemented")


@product_router.post("/upload")
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


@product_router.get("/upload/{job_id}")
def upload_status(job_id: str, request: Request, response: Response) -> dict:
    """The real parse-job status for ``job_id`` under the resolved tenant."""
    tenant_id = _require_product_tenant(request)
    response.headers["Cache-Control"] = "no-store"
    return job_status_payload(tenant_id, job_id)


@product_router.post("/answer")
def answer_product_question(request: Request, body: AnswerRequest) -> dict:
    """Answer a product question kernel-first, else grounded in attached chunks.

    This is where ``answer_query`` actually runs for a GENERATED product: the
    tenant is resolved from the authenticated principal, and the shared helper
    (reused by the Floor route) calls the kernel answer path. A kernel-covered
    question returns the kernel's COMPUTED number; an out-of-kernel one returns
    a labelled, cited improvisation; no retrieval backend returns a surfaced
    503 "retrieval backend not configured".
    """
    tenant_id = _require_product_tenant(request)
    return answer_question(tenant_id, body)


@product_router.post("/overlays")
def submit_formula_overlay(request: Request, body: Dict[str, Any]) -> dict:
    """K-F5: store one overlay set for the tenant after the kernel validates it.

    A set the kernel's precedence rule refuses answers 422 carrying the
    PrecedenceError reason; a body that is not an overlay set answers 422 too.
    Nothing is stored on refusal.
    """
    from .formula_intake import OverlayShapeError, PrecedenceError, submit_overlay

    tenant_id = _require_product_tenant(request)
    try:
        return submit_overlay(tenant_id, body)
    except (PrecedenceError, OverlayShapeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@product_router.get("/overlays")
def list_formula_overlays(request: Request, response: Response) -> dict:
    """K-F5: the tenant's stored overlay sets."""
    from .formula_intake import list_overlays

    tenant_id = _require_product_tenant(request)
    response.headers["Cache-Control"] = "no-store"
    return list_overlays(tenant_id)


# The combined router: the REAL routes plus the 501 placeholders. Used on the
# Factory host and by the full-router tests (only the Drive routes still 501).
# The deploy renderer imports ``product_router`` (real routes ONLY).
router = APIRouter()
router.include_router(product_router)
router.include_router(_scaffold_router)


# ── shared ingest helpers (reused by the Factory-floor session route) ───────
#
# One validate -> store -> queue pipeline for BOTH surfaces (the generated
# product's /v1/product/ingestion/upload and the pilot's Floor upload). The
# caller resolves the tenant id and passes it in; these never read headers.


async def ingest_uploads(
    tenant_id: str, files: Optional[List["UploadFile"]]
) -> dict:
    """Validate, store, and parse each uploaded file inline.

    Returns the flat single-file shape for one file, or ``{items, count}`` for
    several. 400 when no file was sent (mirrors ``app.routers.upload``). Parsing
    runs by default: after the validate-all-before-store pass, each stored
    object is parsed inline and UNCONDITIONALLY, so the reported status reflects
    the real post-parse state (``parsed`` / ``failed``). The parser never raises
    out, so a parse failure lands the job at ``failed``, not a 500. ``queued``
    survives only if the request is interrupted before this parse runs — it is
    never the normal post-upload response state.
    """
    from .object_store import save_object
    from .parse_job import ParseStatus, create_job
    from .parsers import parse_object
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
        parsed = parse_object(tenant_id, stored.object_id, job_id=job.job_id)
        status_value = parsed.status.value
        items.append(
            {
                "object_id": stored.object_id,
                "job_id": job.job_id,
                "status": status_value,
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
        # Whether the parsed chunks reached retrieval (F-1c), SURFACED.
        "index_state": job.index_state,
    }


def answer_question(tenant_id: str, body: "AnswerRequest") -> Dict[str, Any]:
    """Shared answer helper for BOTH product surfaces (kernel route + Floor route).

    Mirrors how ``ingest_uploads`` backs both ``/upload`` routes: the caller
    resolves the tenant and passes it in; this never reads headers. It calls the
    kernel's :func:`answer_query` and maps its refusals to surfaced HTTP errors:

    * 503 when retrieval cannot run — either no engine is installed
      (``RETRIEVAL_BACKEND_NOT_CONFIGURED``) OR the embeddings egress itself is
      unconfigured / failing (``EmbeddingEgressError``). This is mapped by
      EXCEPTION TYPE, not by message text, so both an unconfigured egress and a
      configured-but-failing egress surface a 503 — never a 200 that hides the
      broken backend behind a fabricated "missing inputs", and never a 500.
    * 503 ALSO when the host NAMED a non-semantic (keyword) embedder — a
      SERVER-config error raised as ``RetrievalBackendMisconfigured`` carrying
      the named refusal ("keyword_embedder_not_retrieval: ..."). Matched by
      TYPE, BEFORE the plain-grounding handler, so a misconfigured backend is a
      503, never a 422.
    * 422 ONLY when retrieval ran but the answer cannot be grounded (a plain
      ``RetrievalEngineError`` — e.g. "not in the retrieved excerpts"). That is
      the CLIENT's grounding refusal, the one case that is 422.

    The returned dict is ``ProductAnswer.to_dict()``.
    """
    from app.cerebrum_product_kernel.answer import answer_query
    from app.cerebrum_product_kernel.ingestion.retrieval_index import (
        RETRIEVAL_BACKEND_NOT_CONFIGURED,
    )
    from app.cerebrum_product_kernel.retrieval_engine import (
        RetrievalBackendMisconfigured,
        RetrievalEngineError,
    )
    from app.core.llm_embeddings import EmbeddingEgressError

    try:
        from app.cerebrum_product_kernel.ingestion.formula_intake import tenant_overlays

        answer = answer_query(
            tenant_id, body.query, body.answer_text, overlays=tenant_overlays(tenant_id)
        )
    except EmbeddingEgressError as exc:
        # The embeddings egress is unconfigured or failed — a computed 503,
        # matched by TYPE (not message), on both answer routes.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RetrievalBackendMisconfigured as exc:
        # SERVER-config error (a keyword embedder was NAMED): 503 carrying the
        # named refusal, matched by TYPE before the plain-grounding handler.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        if RETRIEVAL_BACKEND_NOT_CONFIGURED in message:
            raise HTTPException(status_code=503, detail=message) from exc
        raise
    except RetrievalEngineError as exc:
        # CLIENT grounding refusal (nothing retrieved supports the claim): the
        # one 422 case — a surfaced "not in the retrieved excerpts", not a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return answer.to_dict()
