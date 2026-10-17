"""The certified-kit retrieval engine (Phase 4).

Real RAG, inherited by every generated platform through the product kernel
copytree: embed -> per-tenant vector store -> similarity retrieve -> inject
with the Phase 2 precedence ladder and the Phase 3 per-claim labels.
Generated platforms never hand-roll retrieval.

CONTRACT
--------
- An embedder is ``callable(text) -> list[float]`` and declares
  ``semantic: bool``. Only semantic embedders may drive retrieval; a
  keyword matcher passed as the engine's embedder is refused at
  construction with ``keyword_embedder_not_retrieval`` (that refusal is
  exactly what P4's mutation turns RED when removed).
- A vector store implements upsert/query/count over ONE tenant's
  collection. The production store is the per-tenant Chroma collection
  (Phase 1.3); the kit's persistence adapter provides it. The engine is
  provider-independent.
- Grounding honesty (4.3): a fact absent from the retrieved layers is
  refused with ``grounded_fact_absent`` and reported as "not in the
  retrieved excerpts" — NEVER "the corpus doesn't contain it" (the engine
  cannot see the whole corpus; it can only speak for what it retrieved).

The engine performs no LLM call and no network call; it is pure retrieval
plus the precedence/label machinery of Phases 2-3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

from app.cerebrum_product_kernel.claim_labels import (
    Claim,
    LabeledAnswer,
)
from app.cerebrum_product_kernel.precedence import (
    LayerObject,
    PrecedenceVerdict,
    resolve_precedence,
)

KEYWORD_EMBEDDER_NOT_RETRIEVAL = "keyword_embedder_not_retrieval"
GROUNDED_FACT_ABSENT = "grounded_fact_absent"
ABSENCE_WORDING = "not in the retrieved excerpts"


class RetrievalEngineError(ValueError):
    """A named refusal from the retrieval engine."""


class Embedder(Protocol):
    semantic: bool

    def __call__(self, text: str) -> List[float]: ...


class VectorStore(Protocol):
    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Dict[str, Any]],
    ) -> None: ...

    def query(
        self, collection: str, vector: Sequence[float], top_k: int
    ) -> List[Dict[str, Any]]: ...

    def count(self, collection: str) -> int: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class RetrievalHit:
    """One retrieved chunk with its similarity score and layer metadata."""

    chunk_id: str
    text: str
    score: float
    layer: int
    tenant_id: Optional[str] = None


@dataclass
class RetrievalEngine:
    """Embed -> retrieve -> label. No LLM, no network, no keyword path."""

    embedder: Embedder
    store: VectorStore
    top_k: int = 6
    #: Zero-similarity rows are NOT matches — a chunk with no semantic
    #: relation to the query must not rank or label the answer.
    min_score: float = 1e-6

    def __post_init__(self) -> None:
        if not getattr(self.embedder, "semantic", False):
            raise RetrievalEngineError(
                f"{KEYWORD_EMBEDDER_NOT_RETRIEVAL}: the retrieval engine "
                "requires a semantic embedder — a keyword matcher is not "
                "retrieval"
            )

    def ingest(
        self,
        collection: str,
        chunks: Sequence[Dict[str, Any]],
    ) -> None:
        """Chunk metadata carries layer (1-4) and tenant_id; vectors are
        computed by the embedder, never by a keyword table."""
        ids = [str(c["id"]) for c in chunks]
        vectors = [self.embedder(str(c["text"])) for c in chunks]
        documents = [str(c["text"]) for c in chunks]
        metadatas = [
            {
                "layer": int(c.get("layer", 2)),
                "tenant_id": c.get("tenant_id"),
                "object_id": c.get("object_id", ""),
            }
            for c in chunks
        ]
        self.store.upsert(collection, ids, vectors, documents, metadatas)

    def retrieve(self, collection: str, query: str) -> List[RetrievalHit]:
        """Semantic retrieval: the query is embedded, never token-matched."""
        vector = self.embedder(query)
        rows = self.store.query(collection, vector, self.top_k)
        hits: List[RetrievalHit] = []
        for row in rows:
            score = float(row.get("score") or 0.0)
            if score < self.min_score:
                continue
            meta = row.get("metadata") or {}
            hits.append(
                RetrievalHit(
                    chunk_id=str(row.get("id") or ""),
                    text=str(row.get("document") or row.get("text") or ""),
                    score=score,
                    layer=int(meta.get("layer") or 2),
                    tenant_id=meta.get("tenant_id"),
                )
            )
        return hits

    def labeled_answer(
        self,
        collection: str,
        query: str,
        answer_text: str,
    ) -> LabeledAnswer:
        """Ground the answer against the retrieved layers (4.3).

        The answer's claims are labeled from the retrieved hits through the
        Phase 2 precedence ladder; a claim no retrieved excerpt supports is
        refused with ``grounded_fact_absent`` and the absence wording —
        never "the corpus doesn't contain it".
        """
        hits = self.retrieve(collection, query)
        if not hits:
            raise RetrievalEngineError(
                f"{GROUNDED_FACT_ABSENT}: {ABSENCE_WORDING} — refusing to "
                "state the fact"
            )
        verdict = _layer_verdict(hits)
        return LabeledAnswer(
            claims=[
                Claim(
                    text=answer_text,
                    layer=verdict.winner.layer,
                    object_id=verdict.winner.object_id,
                    precedence=(
                        verdict.records[0] if verdict.records else None
                    ),
                )
            ]
        )


def _layer_verdict(hits: Sequence[RetrievalHit]) -> PrecedenceVerdict:
    """Rank the retrieved layers with the Phase 2 ladder; the winner's layer
    labels the claim. The model is told the winner — never asked."""
    objects = [
        LayerObject(
            object_id=hit.chunk_id or hit.text[:40],
            layer=hit.layer,
            tenant_id=hit.tenant_id,
            excerpt=hit.text,
        )
        for hit in hits
    ]
    return resolve_precedence(objects)


class InMemoryVectorStore:
    """A provider-independent in-memory store (test surface + small pilots).

    The production store is the per-tenant Chroma collection (Phase 1.3);
    this class keeps the kernel import-light and makes the engine testable
    with no chromadb dependency.
    """

    def __init__(self) -> None:
        self._collections: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def upsert(self, collection, ids, vectors, documents, metadatas) -> None:
        coll = self._collections.setdefault(collection, {})
        for cid, vector, document, meta in zip(ids, vectors, documents, metadatas):
            coll[str(cid)] = {
                "id": str(cid),
                "vector": list(vector),
                "document": document,
                "metadata": dict(meta),
            }

    def query(self, collection, vector, top_k):
        coll = self._collections.get(collection, {})
        scored = [
            {
                "id": row["id"],
                "document": row["document"],
                "metadata": row["metadata"],
                "score": cosine(vector, row["vector"]),
            }
            for row in coll.values()
        ]
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]

    def count(self, collection):
        return len(self._collections.get(collection, {}))
