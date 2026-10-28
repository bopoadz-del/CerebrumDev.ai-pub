"""Ingest F-1b's parsed chunks into the tenant's RetrievalEngine (F-1c).

F-1a stored the upload; F-1b parsed it into located chunks persisted at
``jobs/<job_id>/chunks.json``. F-1c is the step that makes those chunks REACH
retrieval: when a parse completes, its chunks are ingested (layer 2, documents)
into the tenant's own :class:`RetrievalEngine` collection, so ``retrieve()`` can
return them with their file + location for citation.

ISOLATION
---------
The retrieval collection is named by the SAME server-derived digest the upload
store and the build binding use (``tenant_bind.bind_tenant_store(...).tenant_key``),
so an attach, a build, and this retrieval index share one isolation boundary by
construction — one tenant's collection name is unreachable from another tenant's
identity. The collection name is never a client-supplied value.

PROVIDER SEAM (why there is no hand-rolled embedder here)
---------------------------------------------------------
``RetrievalEngine`` is provider-independent by contract: a SEMANTIC embedder and
a per-tenant vector store are injected by the host kit's persistence adapter —
the product kernel must not bake a keyword matcher in (that is refused at the
engine's own construction). So this module resolves the per-tenant engine through
a pluggable provider: a generated platform wires its configured embedder + Chroma
collection; tests inject a fixture engine. When no engine is configured (the bare
Factory repo, which ships no product embedder), ingest-on-parse is a no-op that is
logged, never a crash — exactly as the parse job must never fail for want of a
retrieval backend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.cerebrum_product_kernel.retrieval_engine import RetrievalEngine
from app.factory.build.tenant_bind import bind_tenant_store

logger = logging.getLogger(__name__)

#: The surfaced message when retrieval has no configured backend. The ingest
#: path folds it into a job's index-state; the answer path refuses with it.
RETRIEVAL_BACKEND_NOT_CONFIGURED = "retrieval backend not configured"

#: Job index-state values recorded on the parse job (never a silent no-op).
INDEX_STATE_INDEXED = "indexed"
INDEX_STATE_NOT_INDEXED = "not_indexed"


@dataclass
class IndexOutcome:
    """The SURFACED result of indexing a parse job's chunks.

    ``state`` is ``"indexed"`` when the chunks reached the engine, else
    ``"not_indexed"`` with a human ``detail`` (the retrieval-backend message or
    the engine's own error). Recorded on the parse job so the client can see
    whether an upload is retrievable — never a debug-only no-op.
    """

    state: str
    detail: str = ""
    count: int = 0

    @property
    def index_state(self) -> str:
        """The compact string stamped on the parse-job record."""
        if self.state == INDEX_STATE_INDEXED:
            return INDEX_STATE_INDEXED
        if self.detail:
            return f"{INDEX_STATE_NOT_INDEXED}: {self.detail}"
        return INDEX_STATE_NOT_INDEXED


#: A provider resolves the per-tenant engine, or returns ``None`` when no
#: product embedder/store is configured in this host.
EngineProvider = Callable[[str], Optional[RetrievalEngine]]


#: No provider until the host installs one. ``None`` means "no product
#: retrieval backend configured here": the SHIPPED behaviour installs a real
#: per-tenant engine at host boot (``engine_install.install_default_engine``);
#: before that runs, or after a test clears the provider, ``tenant_engine``
#: returns ``None`` so the ingest path records a surfaced ``not_indexed`` state
#: and the answer path refuses — never a silent empty.
_ENGINE_PROVIDER: Optional[EngineProvider] = None


def set_engine_provider(provider: Optional[EngineProvider]) -> None:
    """Install (or, with ``None``, clear) the per-tenant engine provider.

    The host kit calls this once at boot with a provider that builds the
    configured semantic embedder + per-tenant Chroma collection; tests use it
    to inject a fixture engine. Resetting to ``None`` restores the no-backend
    default: ``tenant_engine`` then returns ``None`` and ingest-on-parse records
    a surfaced ``not_indexed`` state.
    """
    global _ENGINE_PROVIDER
    _ENGINE_PROVIDER = provider


def tenant_collection(identity: str) -> str:
    """The retrieval collection name for ``identity``.

    It is the SAME digest ``tenant_bind`` binds the upload store under, so the
    retrieval index isolates on the exact boundary the blobs and parse jobs do.
    Refuses an empty identity rather than defaulting one.
    """
    binding = bind_tenant_store(str(identity or "").strip())
    if binding is None:
        raise ValueError(
            "tenant identity must be resolved by the caller from the "
            "authenticated principal, never defaulted"
        )
    return binding.tenant_key


def tenant_engine(identity: str) -> Optional[RetrievalEngine]:
    """Resolve the per-tenant engine through the installed provider.

    ``None`` when no provider is installed (no product retrieval backend
    configured in this host) — the caller surfaces that, never a silent empty.
    """
    provider = _ENGINE_PROVIDER
    if provider is None:
        return None
    return provider(str(identity or "").strip())


def ingest_parsed_chunks(
    identity: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    engine: Optional[RetrievalEngine] = None,
) -> IndexOutcome:
    """Ingest a parse job's chunks into the tenant's retrieval collection.

    Returns a SURFACED :class:`IndexOutcome` (``indexed`` / ``not_indexed`` with
    a reason) so the caller can stamp it on the parse job — the indexing result
    is never a silent debug no-op. Reuses :meth:`RetrievalEngine.ingest`
    unchanged — the Chroma/isolation logic is NOT duplicated here; this only
    chooses the tenant-scoped collection name and hands the chunks over. Each
    chunk keeps its ``file`` + ``location`` so retrieval can cite them.

    Best-effort by contract: a retrieval-backend failure must NEVER fail the
    parse job. An engine error (egress not configured, store error) is captured
    into the outcome's ``not_indexed`` reason rather than raised, so the upload
    stays ``parsed``. It still raises for a programmer error the caller should
    see (an unresolved identity), matching the rest of the ingestion layer.
    """
    rows: List[Dict[str, Any]] = [c for c in (chunks or []) if c]
    if not rows:
        return IndexOutcome(state=INDEX_STATE_NOT_INDEXED, detail="no chunks", count=0)
    engine = engine if engine is not None else tenant_engine(identity)
    if engine is None:
        return IndexOutcome(
            state=INDEX_STATE_NOT_INDEXED,
            detail=RETRIEVAL_BACKEND_NOT_CONFIGURED,
            count=0,
        )
    collection = tenant_collection(identity)
    try:
        engine.ingest(collection, rows)
    except Exception as exc:  # noqa: BLE001 — surfaced on the job, never a crash
        logger.warning("ingest-on-parse: engine refused %d chunk(s): %s", len(rows), exc)
        return IndexOutcome(
            state=INDEX_STATE_NOT_INDEXED, detail=str(exc), count=0
        )
    logger.debug("ingest-on-parse: indexed %d chunk(s) into %s", len(rows), collection)
    return IndexOutcome(state=INDEX_STATE_INDEXED, count=len(rows))
