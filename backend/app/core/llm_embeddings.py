"""Embeddings over THIS repo's single LLM egress (F-1c, no new egress path).

The retrieval engine needs a SEMANTIC embedder. Rather than add a model
download, a native wheel, or a second network door, this module reuses the
exact egress the Floor chat already owns: :func:`app.core.llm_config.get_llm_config`
resolves the OpenAI-compatible ``{base_url, api_key, model}`` triple, and the
embeddings call is the same provider's ``POST {base_url}/embeddings`` — the
sibling of the ``/chat/completions`` call in ``chain_generator``. No new SDK
(``httpx`` is already a dependency), no new host (same ``base_url``), no new
credential (same key).

A caller that has no embeddings backend configured gets a surfaced
:class:`EmbeddingEgressError`, never a silent empty — the ingestion and answer
paths turn that into a visible "retrieval backend not configured" state.

The single network call is isolated in :func:`_request_embeddings` so a test
can stub it with a deterministic, input-derived fake and never touch the wire.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

from app.core.llm_config import get_llm_config

#: Embeddings model name. OpenAI-compatible hosts expose a different model id
#: for embeddings than for chat, so it is its own env; it falls back to the
#: chat model only so a mis-set deployment surfaces a provider 4xx rather than
#: a silent default. NEVER a customer/vertical constant — a repo coordinate.
EMBEDDINGS_MODEL_ENV = "CEREBRUM_EMBEDDINGS_MODEL"


class EmbeddingEgressError(RuntimeError):
    """The embeddings egress is not configured, or the provider refused."""


def embeddings_model(cfg: Dict[str, Any]) -> str:
    env = os.getenv(EMBEDDINGS_MODEL_ENV, "").strip()
    if env:
        return env
    return str(cfg.get("model") or "").strip()


def _request_embeddings(
    url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float
) -> Dict[str, Any]:
    """The one network call. Stubbed in tests with an input-derived fake."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def embed_texts(texts: List[str], *, timeout: float = 60.0) -> List[List[float]]:
    """Embed ``texts`` through the single LLM egress; one vector per text.

    Raises :class:`EmbeddingEgressError` when no base_url/key/model is
    configured (so the caller can surface "retrieval backend not configured"),
    or when the provider refuses the request.
    """
    rows = [str(t) for t in (texts or [])]
    if not rows:
        return []
    cfg = get_llm_config()
    base_url = str(cfg.get("base_url") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    model = embeddings_model(cfg)
    if not base_url or not api_key or not model:
        raise EmbeddingEgressError(
            "retrieval backend not configured: no embeddings egress "
            "(base_url/api_key/model) resolved from the LLM config"
        )
    url = f"{base_url.rstrip('/')}/embeddings"
    headers = {"Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {api_key}"
    payload: Dict[str, Any] = {"model": model, "input": rows}
    try:
        data = _request_embeddings(url, headers, payload, timeout)
    except EmbeddingEgressError:
        raise
    except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed here
        raise EmbeddingEgressError(f"embeddings egress failed: {exc}") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != len(rows):
        raise EmbeddingEgressError(
            "embeddings egress returned an unexpected shape "
            f"({'no data array' if not isinstance(items, list) else 'count mismatch'})"
        )
    out: List[List[float]] = []
    for item in items:
        vec = (item or {}).get("embedding") if isinstance(item, dict) else None
        if not isinstance(vec, list) or not vec:
            raise EmbeddingEgressError("embeddings egress returned an empty vector")
        out.append([float(x) for x in vec])
    return out
