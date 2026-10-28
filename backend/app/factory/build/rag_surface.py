"""Factory-grounded dual-RAG HTTP surface (PHASE 2 keep-path).

#407 aligned PHASE 2 DO/ACCEPTANCE with the checker. Tip ``9df9b15``
Steward Continue ``sess_5782f226`` run6 still ``FAILED_ROLE_ERROR`` at
``writer_phase_frontend_rag``: C-BRIEF #2 ``work=6`` ``gaps_only`` completed
via kimi CLI, ingest/query still unquoted in ``app/**/*.py``.

Cause (not a brief typo): ``cbrief_work_ids`` is GENERATE gaps plus REUSE
hole-fill capability ids. PHASE 2 ``phase_step0_line`` said ``Author only
named GAPS``. ``dual_rag_*`` binds are REUSE (``vector_search`` /
``knowledge``), so the CLI never saw ``/v1/rag/ingest`` + ``/v1/rag/query``
as WRITES. Contract prose is not a work item.

Same honesty as CLONER planting blocks: Factory owns this emit. Not an
empty stub, not a deleted check, not timeout-as-success. Plant real
callable FastAPI ingest (accepts a body) + query (structured hits), wire
them into ``app.main``, leave the checker looking for quoted paths.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, List

from app.factory.build.writer_phases import (
    inventory_needs_rag,
    rag_ingest_route_present,
    rag_query_route_present,
)

FACTORY_GROUNDED_RAG_SOURCE = "factory-grounded rag surface"
RAG_ROUTES_REL = Path("app") / "rag_routes.py"
RAG_WIRE_MARK = "from app.rag_routes import router as rag_router"

#: Self-contained keep-path module. STORAGE_PATH JSONL + FastAPI routes.
#: Ingest takes the document text in writer_phases.RAG_INGEST_TEXT_FIELDS
#: (the body fields the brief declares); test_declared_contract_sweep pins it.
#: Query GET+POST returns structured hits with excerpt/score.
_RAG_ROUTES_PY = '''"""Factory-grounded dual-RAG surface (PHASE 2 keep-path).

Authorship: factory-grounded rag surface. Not a CLI template and not an empty
stub. Ingest stores a document. The query/answer surface is RE-POINTED (F-1d)
to the product's REAL answer path — the shared kernel helper (answer_question
-> answer_query): kernel-first, retrieval through the installed engine, real
citations, and the 503/422 split. The old keyword-overlap matcher is RETIRED:
/v1/rag/query is now a tenant-scoped alias of the real /answer verb, never a
placeholder and never a 501.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["rag-surface"])
_LOCK = threading.Lock()


def _storage_root() -> Path:
    root = Path(os.environ.get("STORAGE_PATH") or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path() -> Path:
    return _storage_root() / "rag_index.jsonl"


def persist_document(record: Dict[str, Any]) -> Dict[str, Any]:
    path = _index_path()
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\\n")
    return record


class RagIngestRequest(BaseModel):
    text: Optional[str] = None
    content: Optional[str] = None
    paragraph: Optional[str] = None
    query: Optional[str] = None
    title: Optional[str] = None
    doc_id: Optional[str] = None
    layer: int = Field(1, ge=1, le=2)
    property_id: Optional[str] = None


class RagQueryRequest(BaseModel):
    q: Optional[str] = None
    query: Optional[str] = None
    text: Optional[str] = None
    answer_text: str = ""
    top_k: int = Field(5, ge=1, le=20)


def _body_text(body: RagIngestRequest) -> str:
    for candidate in (body.text, body.content, body.paragraph, body.query):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _query_text(q: Optional[str], body: Optional[RagQueryRequest] = None) -> str:
    if q and str(q).strip():
        return str(q).strip()
    if body is None:
        return ""
    for candidate in (body.q, body.query, body.text):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


@router.post("/v1/rag/ingest")
def rag_ingest(body: RagIngestRequest) -> Dict[str, Any]:
    text = _body_text(body)
    if not text:
        return {"ok": False, "error": "ingest body needs text, content, or paragraph"}
    doc_id = (body.doc_id or "").strip() or ("doc-" + uuid.uuid4().hex[:12])
    title = (body.title or "").strip() or doc_id
    record = persist_document(
        {
            "doc_id": doc_id,
            "title": title,
            "text": text,
            "layer": body.layer,
            "property_id": body.property_id,
        }
    )
    return {
        "ok": True,
        "document_id": record["doc_id"],
        "title": record["title"],
        "layer": record["layer"],
        "stored": True,
        "chunk_count": 1,
        "source": "factory-grounded rag surface",
    }


def _resolve_tenant(request: Request) -> str:
    """Resolve the bound tenant through the product's single tenancy path.

    The answer-over-corpus surface is tenant-scoped exactly like the kernel
    ingestion routes: a missing / unknown / client-named tenant is refused by
    name with 401, never mapped to a default.
    """
    import app.tenancy as tenancy

    try:
        tenant = tenancy.resolve_tenant(request.headers)
    except tenancy.TenantRefused:
        raise HTTPException(status_code=401, detail="authentication_required")
    return tenant.tenant_id


def _real_answer(tenant_id: str, query: str, answer_text: str) -> Dict[str, Any]:
    """Route the query through the product's REAL answer path (F-1d re-point).

    The SAME shared helper the kernel ingestion /answer verb uses: kernel-first,
    retrieval through the installed engine, real citations, and the 503/422
    split. There is NO keyword-overlap fallback — an unconfigured retrieval
    backend fails closed with the computed "retrieval backend not configured",
    never a silent keyword match.
    """
    try:
        from app.cerebrum_product_kernel.ingestion.router import (
            AnswerRequest,
            answer_question,
        )
    except ImportError as exc:  # pragma: no cover - a product always vendors the kernel
        raise HTTPException(
            status_code=503,
            detail="retrieval backend not configured: product kernel not available",
        ) from exc
    return answer_question(
        tenant_id, AnswerRequest(query=query, answer_text=answer_text or "")
    )


@router.get("/v1/rag/query")
def rag_query_get(
    request: Request,
    q: str = Query(..., min_length=1),
    answer_text: str = Query(""),
) -> Dict[str, Any]:
    return _real_answer(_resolve_tenant(request), q, answer_text)


@router.post("/v1/rag/query")
def rag_query_post(request: Request, body: RagQueryRequest) -> Dict[str, Any]:
    query = _query_text(None, body)
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    return _real_answer(_resolve_tenant(request), query, body.answer_text)
'''


def emit_factory_grounded_rag_surface(root: Path, compiled: Any) -> List[str]:
    """Plant callable ingest/query routes when inventory names rag/dual_rag.

    Skips when the checker already sees quoted ingest+query paths (CLI or
    kit landed them). Does not write GENERATE/GAP persist handlers. Does
    not claim a CLI session.
    """
    root = Path(root)
    if not inventory_needs_rag(compiled):
        return []
    if rag_ingest_route_present(root) and rag_query_route_present(root):
        return []
    dest = root / RAG_ROUTES_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_RAG_ROUTES_PY, encoding="utf-8")
    wire_rag_router(root)
    return ["rag_ingest", "rag_query"]


def wire_rag_router(root: Path) -> bool:
    """Include the planted router on ``app.main`` without a ``/v1`` prefix.

    RoleRunner capability routes use ``include_router(..., prefix="/v1")``.
    These keep-path paths are already ``/v1/rag/*`` (same as the estate kit).
    """
    main = Path(root) / "app" / "main.py"
    if not main.is_file():
        return False
    text = main.read_text(encoding="utf-8")
    # Already wired = main.py imports the keep-path module (syntax tree).
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None and any(
        (isinstance(n, ast.ImportFrom) and n.module == "app.rag_routes")
        or (isinstance(n, ast.Import) and any(a.name == "app.rag_routes" for a in n.names))
        for n in ast.walk(tree)
    ):
        return False
    snippet = (
        "\ntry:\n"
        f"    {RAG_WIRE_MARK}\n"
        "\n"
        "    app.include_router(rag_router)\n"
        "except ImportError:\n"
        "    pass\n"
    )
    needle = 'app.include_router(router, prefix="/v1")'
    if needle in text:
        text = text.replace(needle, needle + "\n" + snippet, 1)
    else:
        text = text.rstrip() + "\n" + snippet
    main.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return True
