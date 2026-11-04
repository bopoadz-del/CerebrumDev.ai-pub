"""Local semantic embedder: BAAI/bge-small-en-v1.5, in-process, no LLM egress.

ORDERS_02NOV_2320Z — a product build must embed with a LOCAL model, never by
asking the chat provider for ``/embeddings`` (DeepSeek, the chat provider, has
no embeddings endpoint -> 404, which stalled the first pilot build).

This mirrors the-shovel's local-embedder choice (``app/core/rag/embeddings.py``
there: ``DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"``, 384-dim, loaded
once per process, L2-normalised) but through **fastembed**, the ONNX embedder
this image already ships (``requirements.txt``: fastembed + onnxruntime). That
is deliberate: this backend omits torch on purpose (see requirements.txt), and
fastembed runs the *same* bge-small weights at the *same* 384 dims without it.
No new dependency, no torch, no silent fallback.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import List

import numpy as np

#: The one default model. One place so the provider, the dim check and every
#: caller agree. Overridable only by passing an explicit ``model_name``.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
#: bge-small-en-v1.5 is 384-dim. The vector store is sized to this, so a model
#: that returns a different width is a hard error, never a silent mismatch.
EMBEDDING_DIM = 384

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _model(name: str = DEFAULT_EMBEDDING_MODEL):
    """The process-cached fastembed model. Loading it is slow and large, so one
    instance per process. Imported lazily so importing this module pulls in no
    native wheel or model load."""
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=name)


def embed_texts(texts: List[str], model_name: str = DEFAULT_EMBEDDING_MODEL) -> np.ndarray:
    """Unit-normalised float32 vectors, shape ``(len(texts), EMBEDDING_DIM)``.

    A pure function of its input: same texts -> same vectors. Empty input ->
    an empty ``(0, EMBEDDING_DIM)`` array. A model that returns the wrong width
    raises, so a dim mismatch can never corrupt the store silently.
    """
    items = [str(t) for t in (texts or [])]
    if not items:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    with _lock:
        raw = list(_model(model_name).embed(items))
    vecs = np.asarray(raw, dtype=np.float32)
    if vecs.ndim != 2 or vecs.shape[1] != EMBEDDING_DIM:
        raise RuntimeError(
            f"embedder {model_name} returned shape {tuple(vecs.shape)}, "
            f"expected (*, {EMBEDDING_DIM})"
        )
    # L2-normalise explicitly so cosine similarity is a pure dot product,
    # regardless of the backend's own normalisation default.
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (vecs / norms).astype(np.float32)
