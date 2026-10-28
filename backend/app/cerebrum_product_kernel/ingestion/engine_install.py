"""Install the REAL per-tenant retrieval engine at host boot (F-1c BAR 1).

There is no off-by-default seam any more. At boot the host calls
:func:`install_default_engine`, which registers an engine provider that builds a
per-tenant :class:`RetrievalEngine` from:

* the configured SEMANTIC embedder — resolved from the embedding registry
  (``app.core.rag_embedding_providers``). The DEFAULT is the one real provider
  (:data:`~app.core.rag_embedding_providers.LLM_SEMANTIC_V1`), which embeds
  through this repo's single LLM egress. An env (:data:`EMBEDDER_ENV`) may NAME
  a different registered provider, but the default is never "none"; and
* the per-tenant Chroma collection — :class:`ChromaVectorStore`, keyed by the
  collection name the engine is handed (the ``tenant_bind`` digest).

Whether retrieval actually indexes at runtime depends on the egress being
configured (a key + model). When it is not, ``embed_texts`` raises and the
ingestion path records a SURFACED ``not_indexed`` state and the answer path
refuses with "retrieval backend not configured" — never a silent no-op.
"""

from __future__ import annotations

import os
from typing import List, Optional

from app.cerebrum_product_kernel.retrieval_engine import (
    Embedder,
    RetrievalBackendMisconfigured,
    RetrievalEngine,
    RetrievalEngineError,
)

from . import retrieval_index
from .chroma_vector_store import ChromaVectorStore

#: Env that may NAME the registered embedding provider to drive retrieval. The
#: DEFAULT is the real semantic provider; it is NEVER "none".
EMBEDDER_ENV = "PRODUCT_RETRIEVAL_EMBEDDER"


class _ProviderEmbedder:
    """Adapt a registry ``RagEmbeddingProvider`` to the engine's Embedder.

    The engine wants ``callable(text) -> list[float]`` with a ``semantic``
    flag; the registry provider exposes ``embed_texts(list) -> list[list]`` and
    a ``semantic`` attribute. ``semantic`` is copied through so a non-semantic
    provider named by mistake is refused at the engine's own construction.
    """

    def __init__(self, provider) -> None:
        self._provider = provider
        self.semantic = bool(getattr(provider, "semantic", False))

    def __call__(self, text: str) -> List[float]:
        return list(self._provider.embed_texts([str(text)])[0])


def _default_provider_id() -> str:
    from app.core.rag_embedding_providers import LLM_SEMANTIC_V1

    named = os.getenv(EMBEDDER_ENV, "").strip()
    return named or LLM_SEMANTIC_V1


def resolve_embedder() -> Optional[Embedder]:
    """The configured embedder, or ``None`` when the named provider is absent."""
    from app.core.rag_embedding_providers import get_provider

    provider = get_provider(_default_provider_id())
    if provider is None:
        return None
    return _ProviderEmbedder(provider)


def build_tenant_engine(identity: str) -> Optional[RetrievalEngine]:
    """Build the per-tenant engine (embedder + per-tenant Chroma collection).

    Returns ``None`` only when the named provider is ABSENT (an unconfigured
    host — the generic "retrieval backend not configured" path). A provider that
    IS present but is NOT semantic is a different failure: the engine REFUSES it
    by name (``keyword_embedder_not_retrieval``), and that named refusal is
    propagated — never flattened to the generic "not configured" message, and
    never silently accepted as retrieval.
    """
    embedder = resolve_embedder()
    if embedder is None:
        return None
    # A non-semantic provider makes RetrievalEngine raise
    # RetrievalEngineError(keyword_embedder_not_retrieval). That is a SERVER
    # CONFIG error (the host named a keyword embedder), not a client grounding
    # refusal, so it is re-raised as the distinct RetrievalBackendMisconfigured
    # type — carrying the same named text — which the HTTP surface maps to 503
    # (not the 422 a plain grounding RetrievalEngineError gets).
    try:
        return RetrievalEngine(embedder=embedder, store=ChromaVectorStore())
    except RetrievalBackendMisconfigured:
        raise
    except RetrievalEngineError as exc:
        raise RetrievalBackendMisconfigured(str(exc)) from exc


def install_default_engine() -> None:
    """Register the real per-tenant engine provider (host boot, BAR 1)."""
    retrieval_index.set_engine_provider(build_tenant_engine)
