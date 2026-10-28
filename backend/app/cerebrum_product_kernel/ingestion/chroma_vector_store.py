"""A per-tenant Chroma store that conforms to RetrievalEngine's VectorStore.

The RetrievalEngine contract (retrieval_engine.py) wants upsert / query-by-vector
/ count over ONE tenant's collection. The repo already has a ChromaDB client in
``app.core.chroma_store`` — but that one is session-keyed and stores precomputed
embeddings under its own schema, so it is NOT the engine's VectorStore. This is a
SMALL reuse-adapter (not a rewrite): it drives the same ``chromadb`` client, but
exposes exactly the three methods the engine calls, keyed by the collection name
the engine passes (the tenant_bind digest).

``chromadb`` is imported lazily inside the methods so the kernel stays
import-light (the engine's InMemoryVectorStore keeps the no-chromadb test path).

Chroma metadata values must be scalars, but a chunk's ``location`` is a dict, so
the whole metadata mapping is stored as one JSON string (``_meta_json``) and
parsed back on query — the engine gets its metadata dict (layer / file /
location) exactly as it ingested it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_META_JSON = "_meta_json"
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _persist_dir() -> str:
    explicit = os.getenv("CHROMA_PERSIST_DIR", "").strip()
    if explicit:
        return explicit
    storage = os.getenv("STORAGE_PATH", "./storage").strip() or "./storage"
    return str(Path(storage) / "chroma")


def _safe_collection(name: str) -> str:
    """Chroma collection names are constrained (3-512 chars, limited charset).

    The tenant digest is already hex, but sanitise + pad defensively so an
    unusual collection name never raises inside the provider.
    """
    cleaned = _SAFE_RE.sub("_", str(name or "").strip()) or "tenant"
    if len(cleaned) < 3:
        cleaned = (cleaned + "___")[:3]
    return cleaned[:512]


class ChromaVectorStore:
    """VectorStore adapter over a persistent ChromaDB client (reuse, not rewrite)."""

    def __init__(self, persist_dir: Optional[str] = None) -> None:
        self._persist_dir = persist_dir or _persist_dir()
        self._client = None

    def _collection(self, collection: str):
        import chromadb  # lazy: keeps the kernel import-light

        if self._client is None:
            Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self._persist_dir)
        return self._client.get_or_create_collection(
            name=_safe_collection(collection),
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Dict[str, Any]],
    ) -> None:
        col = self._collection(collection)
        col.upsert(
            ids=[str(i) for i in ids],
            embeddings=[[float(x) for x in v] for v in vectors],
            documents=[str(d) for d in documents],
            # The engine's metadata dict (with a nested ``location``) is stored
            # as one JSON string; Chroma only accepts scalar metadata values.
            metadatas=[{_META_JSON: json.dumps(dict(m or {}))} for m in metadatas],
        )

    def query(
        self, collection: str, vector: Sequence[float], top_k: int
    ) -> List[Dict[str, Any]]:
        col = self._collection(collection)
        res = col.query(
            query_embeddings=[[float(x) for x in vector]],
            n_results=max(1, int(top_k or 1)),
            include=["documents", "metadatas", "distances"],
        )
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        rows: List[Dict[str, Any]] = []
        for i in range(len(ids)):
            raw_meta = metas[i] if i < len(metas) else {}
            meta: Dict[str, Any] = {}
            packed = (raw_meta or {}).get(_META_JSON) if isinstance(raw_meta, dict) else None
            if isinstance(packed, str):
                try:
                    meta = json.loads(packed)
                except ValueError:
                    meta = {}
            distance = float(dists[i]) if i < len(dists) and dists[i] is not None else 1.0
            # cosine distance -> similarity; the engine filters on min_score.
            score = 1.0 - distance
            rows.append(
                {
                    "id": str(ids[i]),
                    "document": str(docs[i]) if i < len(docs) else "",
                    "metadata": meta,
                    "score": score,
                }
            )
        return rows

    def count(self, collection: str) -> int:
        return int(self._collection(collection).count())
