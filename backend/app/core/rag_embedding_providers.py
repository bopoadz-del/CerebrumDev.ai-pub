"""Embedding provider contract: the validation-only local provider, and the
ONE real semantic provider backed by this repo's single LLM egress.

The local feature-hash provider stays model-independent (no external API). The
real provider (:data:`LLM_SEMANTIC_V1`) embeds through
``app.core.llm_embeddings`` — the same OpenAI-compatible host the Floor chat
already uses — so it adds no new SDK, native wheel, or network door.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Dict, List, Optional

LOCAL_FEATURE_HASH_V1 = "local_feature_hash_v1"
#: The real semantic provider backed by a LOCAL model (BAAI/bge-small-en-v1.5
#: via fastembed, app.core.embeddings_local). The DEFAULT product embedder: it
#: needs no LLM /embeddings egress, so a chat provider without one (DeepSeek)
#: never stalls a build. ``semantic`` -> drives the RetrievalEngine.
LOCAL_BGE_SMALL_V1 = "local_bge_small_v1"
#: A semantic provider that embeds through THIS repo's LLM egress
#: (app.core.llm_embeddings -> the provider's /embeddings endpoint). Selectable
#: ONLY when the configured provider actually serves /embeddings; it fails loud
#: (never silently) when it does not. Not the default.
LLM_SEMANTIC_V1 = "llm_semantic_v1"


def _egress_status(exc: Exception) -> Optional[int]:
    """The HTTP status carried by an egress error, from the error itself
    (EmbeddingEgressError.status_code) or its underlying response, if any."""
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None) or getattr(
            getattr(exc, "__cause__", None), "response", None
        )
        code = getattr(resp, "status_code", None)
    return int(code) if isinstance(code, int) else None


def _looks_like_no_embeddings_endpoint(exc: Exception) -> bool:
    """True when an egress error means the provider has no /embeddings endpoint,
    decided by the egress HTTP status (404), not by matching message text — a
    404 is "route absent", distinct from a transient 5xx or a 401/403 credential
    failure, which must re-raise as themselves."""
    return _egress_status(exc) == HTTPStatus.NOT_FOUND

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+(?:['-][a-zA-Z0-9]+)*", re.UNICODE)


@dataclass
class RagEmbeddingProvider:
    """Contract for an embedding provider."""

    provider_id: str
    provider_version: str
    algorithm: str
    dimensions: int
    distance_metric: str
    normalization: str
    maximum_batch_size: int
    maximum_input_characters: int
    production_approved: bool

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class _LocalFeatureHashProviderV1(RagEmbeddingProvider):
    """Deterministic signed feature hashing for offline validation.

    Not a semantic embedding model. Used only to validate the embedding
    contract, batching, and persistence layers before introducing a
    production neural provider.
    """

    _VERSION = "1"
    _DIMENSIONS = 384
    _MAX_CHARS = 200_000
    _MAX_BATCH = 64

    def __init__(self) -> None:
        super().__init__(
            provider_id=LOCAL_FEATURE_HASH_V1,
            provider_version=self._VERSION,
            algorithm="signed-feature-hashing",
            dimensions=self._DIMENSIONS,
            distance_metric="cosine",
            normalization="l2",
            maximum_batch_size=self._MAX_BATCH,
            maximum_input_characters=self._MAX_CHARS,
            production_approved=False,
        )
        self._precision = int(os.getenv("RAG_EMBEDDING_VECTOR_PRECISION", "8"))
        if self._precision < 1:
            raise ValueError("RAG_EMBEDDING_VECTOR_PRECISION must be >= 1.")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        if not text:
            raise ValueError("Cannot embed empty text.")
        text = text[: self.maximum_input_characters]
        vector = [0.0] * self.dimensions
        for token in _TOKEN_RE.findall(text.lower()):
            token_bytes = token.encode("utf-8")
            digest = hashlib.sha256(token_bytes).digest()
            dim = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1 if digest[4] % 2 == 0 else -1
            weight = 1.0 + math.log1p(len(token))
            vector[dim] += sign * weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            raise ValueError("Embedding produced a zero vector.")
        vector = [round(v / norm, self._precision) for v in vector]
        # Renormalize after rounding to keep L2 norm ≈ 1.
        rounded_norm = math.sqrt(sum(v * v for v in vector))
        if rounded_norm == 0:
            raise ValueError("Embedding produced a zero vector after rounding.")
        return [round(v / rounded_norm, self._precision) for v in vector]


class _LlmSemanticProviderV1(RagEmbeddingProvider):
    """A REAL semantic embedder: vectors come from the LLM provider's own
    embeddings endpoint via this repo's single egress.

    ``semantic = True`` marks it as retrieval-grade (the RetrievalEngine
    refuses a non-semantic embedder). ``production_approved = True`` because it
    computes genuine model embeddings, not a feature hash. The egress is lazy
    (imported at call time) so importing this module pulls in no network code.
    """

    semantic = True

    _VERSION = "1"
    #: Dimensionality is provider/model-defined and discovered from the first
    #: response; this is a declared upper bound for the contract fields only.
    _DIMENSIONS = 4096
    _MAX_CHARS = 200_000
    _MAX_BATCH = 64

    def __init__(self) -> None:
        super().__init__(
            provider_id=LLM_SEMANTIC_V1,
            provider_version=self._VERSION,
            algorithm="llm-embeddings-openai-compatible",
            dimensions=self._DIMENSIONS,
            distance_metric="cosine",
            normalization="none",
            maximum_batch_size=self._MAX_BATCH,
            maximum_input_characters=self._MAX_CHARS,
            production_approved=True,
        )
        #: Probe the egress once, lazily (first embed), so selecting this
        #: provider against a chat host with no /embeddings fails LOUD, not
        #: silently or as an opaque 404 deep in a build.
        self._probed = False

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        # Lazy import keeps this module free of a hard egress dependency and
        # lets a test stub app.core.llm_embeddings.embed_texts.
        from app.core.llm_embeddings import embed_texts as _embed

        if not self._probed:
            try:
                _embed(["embeddings endpoint probe"])
            except Exception as exc:  # noqa: BLE001 -- classified below, never swallowed
                if _looks_like_no_embeddings_endpoint(exc):
                    raise RuntimeError(
                        "PRODUCT_RETRIEVAL_EMBEDDER=llm_semantic_v1, but the configured "
                        f"chat provider has no /embeddings endpoint ({str(exc)[:200]}). "
                        "Set PRODUCT_RETRIEVAL_EMBEDDER=local_bge_small_v1 (the local model), "
                        "or point the LLM egress at a provider that serves /embeddings."
                    ) from exc
                raise
            self._probed = True
        return _embed(list(texts or []))


class _LocalBgeSmallProviderV1(RagEmbeddingProvider):
    """The DEFAULT product embedder: BAAI/bge-small-en-v1.5 run locally via
    fastembed (app.core.embeddings_local). Genuine 384-dim semantic vectors,
    L2-normalised, no LLM /embeddings egress -- so a chat provider without an
    embeddings endpoint (DeepSeek) never stalls a build. ``semantic = True`` ->
    retrieval-grade; ``production_approved = True``. The model is imported lazily
    so importing this module loads no native wheel."""

    semantic = True

    _VERSION = "1"
    _DIMENSIONS = 384
    _MAX_CHARS = 200_000
    _MAX_BATCH = 64

    def __init__(self) -> None:
        super().__init__(
            provider_id=LOCAL_BGE_SMALL_V1,
            provider_version=self._VERSION,
            algorithm="bge-small-en-v1.5-fastembed",
            dimensions=self._DIMENSIONS,
            distance_metric="cosine",
            normalization="l2",
            maximum_batch_size=self._MAX_BATCH,
            maximum_input_characters=self._MAX_CHARS,
            production_approved=True,
        )

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        from app.core.embeddings_local import embed_texts as _embed

        vecs = _embed(list(texts or []))
        return [[float(x) for x in row] for row in vecs]


_PROVIDERS: Dict[str, RagEmbeddingProvider] = {
    LOCAL_FEATURE_HASH_V1: _LocalFeatureHashProviderV1(),
    LOCAL_BGE_SMALL_V1: _LocalBgeSmallProviderV1(),
    LLM_SEMANTIC_V1: _LlmSemanticProviderV1(),
}


def register_provider(provider: RagEmbeddingProvider) -> None:
    """Register a provider implementation by provider_id."""
    _PROVIDERS[provider.provider_id] = provider


def get_provider(provider_id: str) -> Optional[RagEmbeddingProvider]:
    """Return the provider implementation for provider_id, or None."""
    return _PROVIDERS.get(provider_id)


def list_provider_ids() -> List[str]:
    """Return all registered provider IDs."""
    return list(_PROVIDERS.keys())
