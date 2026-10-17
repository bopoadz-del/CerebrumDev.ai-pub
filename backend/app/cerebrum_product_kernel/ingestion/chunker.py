"""Document text -> RetrievalEngine.ingest() chunk dicts.

Implemented for Phase 2 §1. The splitter is deliberately NAIVE (fixed
~1200-char windows on paragraph boundaries, no overlap) — the module
contract says measure recall on a real corpus before tuning, never the
reverse. The chunk dict shape matches retrieval_engine.py's
RetrievalEngine.ingest() exactly: {id, text, layer, tenant_id, object_id}.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

_TARGET_CHARS = 1200
_LAYERS = (1, 2, 3, 4)


def chunk_document(
    text: str,
    *,
    source_name: str,
    layer: int,
    tenant_id: str,
    object_id: str = "",
) -> List[Dict[str, Any]]:
    """Split ``text`` into RetrievalEngine.ingest()-shaped chunk dicts.

    ``tenant_id`` must come from the authenticated principal (the caller's
    resolver), never from a client-supplied field — this function refuses
    an empty tenant by name rather than defaulting one.
    """
    if not text or not str(text).strip():
        return []
    if layer not in _LAYERS:
        raise ValueError(f"layer must be one of {_LAYERS}, got {layer!r}")
    if not tenant_id or not str(tenant_id).strip():
        raise ValueError(
            "tenant_id must come from the authenticated principal, never "
            "from a client-supplied field"
        )

    blob = str(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", blob) if p.strip()]

    chunks: List[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) + 2 > _TARGET_CHARS:
            chunks.append(buf)
            buf = para
        elif buf:
            buf += "\n\n" + para
        else:
            buf = para
        while len(buf) > _TARGET_CHARS:
            chunks.append(buf[:_TARGET_CHARS])
            buf = buf[_TARGET_CHARS:]
    if buf:
        chunks.append(buf)

    base = hashlib.sha256(
        f"{object_id}:{source_name}:{len(blob)}".encode("utf-8")
    ).hexdigest()[:12]
    return [
        {
            "id": f"{base}:{i:04d}",
            "text": chunk,
            "layer": int(layer),
            "tenant_id": str(tenant_id),
            "object_id": str(object_id or ""),
        }
        for i, chunk in enumerate(chunks)
    ]
