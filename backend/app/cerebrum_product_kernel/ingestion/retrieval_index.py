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
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.cerebrum_product_kernel.retrieval_engine import RetrievalEngine
from app.factory.build.tenant_bind import bind_tenant_store

logger = logging.getLogger(__name__)

#: A provider resolves the per-tenant engine, or returns ``None`` when no
#: product embedder/store is configured in this host.
EngineProvider = Callable[[str], Optional[RetrievalEngine]]


def _no_engine(identity: str) -> Optional[RetrievalEngine]:
    """Default: no product retrieval backend configured in this host."""
    return None


_ENGINE_PROVIDER: EngineProvider = _no_engine


def set_engine_provider(provider: Optional[EngineProvider]) -> None:
    """Install (or, with ``None``, clear) the per-tenant engine provider.

    The host kit calls this once at boot with a provider that builds the
    configured semantic embedder + per-tenant Chroma collection; tests use it
    to inject a fixture engine. Resetting to ``None`` restores the no-backend
    default (ingest-on-parse becomes a logged no-op).
    """
    global _ENGINE_PROVIDER
    _ENGINE_PROVIDER = provider or _no_engine


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
    """Resolve the per-tenant engine through the installed provider."""
    return _ENGINE_PROVIDER(str(identity or "").strip())


def ingest_parsed_chunks(
    identity: str,
    chunks: Sequence[Dict[str, Any]],
    *,
    engine: Optional[RetrievalEngine] = None,
) -> int:
    """Ingest a parse job's chunks into the tenant's retrieval collection.

    Returns the number of chunks ingested (0 when no engine is configured or
    there is nothing to ingest). Reuses :meth:`RetrievalEngine.ingest`
    unchanged — the Chroma/isolation logic is NOT duplicated here; this only
    chooses the tenant-scoped collection name and hands the chunks over. Each
    chunk keeps its ``file`` + ``location`` so retrieval can cite them.

    Best-effort by contract: a retrieval-backend failure must never fail the
    parse job, so the caller (``parsers.parse_object``) wraps this and a raise
    here is swallowed-and-logged there. It still raises for a programmer error
    the caller should see (an unresolved identity), matching the rest of the
    ingestion layer.
    """
    rows: List[Dict[str, Any]] = [c for c in (chunks or []) if c]
    if not rows:
        return 0
    engine = engine if engine is not None else tenant_engine(identity)
    if engine is None:
        logger.debug(
            "ingest-on-parse: no retrieval engine configured for this host; "
            "%d chunk(s) persisted but not indexed",
            len(rows),
        )
        return 0
    collection = tenant_collection(identity)
    engine.ingest(collection, rows)
    logger.debug("ingest-on-parse: indexed %d chunk(s) into %s", len(rows), collection)
    return len(rows)
