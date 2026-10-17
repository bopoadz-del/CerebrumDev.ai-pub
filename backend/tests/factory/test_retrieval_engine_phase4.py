"""Phase 4 acceptance: the certified-kit retrieval engine is real RAG.

T4.1 semantic (not keyword) retrieval; T4.2 generated platforms inherit the
engine; T4.3 absent facts are refused with the honest wording; P4 swaps the
embedder for a keyword matcher and the suite goes RED.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from app.cerebrum_product_kernel.retrieval_engine import (
    ABSENCE_WORDING,
    GROUNDED_FACT_ABSENT,
    KEYWORD_EMBEDDER_NOT_RETRIEVAL,
    InMemoryVectorStore,
    RetrievalEngine,
    RetrievalEngineError,
)


# A deterministic SEMANTIC embedder fixture: synonym groups share vectors,
# so paraphrases with zero lexical overlap still embed to the same point.
# This is semantic-in-miniature; the production embedder is the real model
# provider — what the test proves is that the ENGINE runs the vector path.
_SYNONYM_VECTORS = {
    "price": [1.0, 0.0, 0.0],
    "cost": [1.0, 0.0, 0.0],
    "invoice": [1.0, 0.0, 0.0],
    "maintenance": [0.0, 1.0, 0.0],
    "repair": [0.0, 1.0, 0.0],
    "owner": [0.0, 1.0, 0.0],
}


class SemanticFixtureEmbedder:
    semantic = True

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0, 0.0]
        for word in text.lower().split():
            for synonym, v in _SYNONYM_VECTORS.items():
                if synonym in word:
                    vec = [vec[i] + v[i] for i in range(3)]
        norm = sum(x * x for x in vec) ** 0.5
        if norm:
            vec = [x / norm for x in vec]
        return vec


class KeywordMatcherEmbedder:
    """The P4 mutation: bags the query as raw tokens. NOT semantic."""

    semantic = False

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0, 0.0]
        for word in text.lower().split():
            vec[hash(word) % 3] += 1.0
        norm = sum(x * x for x in vec) ** 0.5
        if norm:
            vec = [x / norm for x in vec]
        return vec


def _engine() -> RetrievalEngine:
    return RetrievalEngine(embedder=SemanticFixtureEmbedder(), store=InMemoryVectorStore())


def _seeded_engine() -> RetrievalEngine:
    engine = _engine()
    engine.ingest(
        "tenant_a",
        [
            {
                "id": "chunk-price",
                "text": "The service price is forty per unit.",
                "layer": 1,
                "object_id": "margin_v1",
            },
            {
                "id": "chunk-maintenance",
                "text": "Maintenance is the operator's responsibility.",
                "layer": 2,
                "tenant_id": "tenant_a",
                "object_id": "maintenance_policy",
            },
        ],
    )
    return engine


# -- T4.1: embeddings, not keywords -------------------------------------------


def test_engine_uses_embeddings_not_keywords():
    """A paraphrased query with zero lexical overlap still retrieves the
    semantically-matching chunk — keyword-only retrieval fails this."""
    engine = _seeded_engine()
    hits = engine.retrieve("tenant_a", "how much does it cost")

    assert hits, "zero-overlap paraphrase must still retrieve"
    assert hits[0].text == "The service price is forty per unit."
    assert hits[0].score > 0.0


def test_keyword_embedder_is_refused_at_construction():
    with pytest.raises(RetrievalEngineError) as exc:
        RetrievalEngine(embedder=KeywordMatcherEmbedder(), store=InMemoryVectorStore())
    assert KEYWORD_EMBEDDER_NOT_RETRIEVAL in str(exc.value)


# -- T4.3: absent facts are refused with the honest wording -------------------


def test_no_fabrication_on_absent_fact():
    engine = _engine()  # nothing ingested
    with pytest.raises(RetrievalEngineError) as exc:
        engine.labeled_answer("tenant_a", "any query", "a fact nobody backed")
    assert GROUNDED_FACT_ABSENT in str(exc.value)
    assert ABSENCE_WORDING in str(exc.value)
    assert "corpus doesn't contain" not in str(exc.value)


# -- the label path: retrieved layers label the claim -------------------------


def test_labeled_answer_uses_the_retrieved_layer():
    engine = _seeded_engine()
    answer = engine.labeled_answer(
        "tenant_a", "how much does it cost", "It costs forty."
    )
    assert answer.claims[0].layer == 1
    assert answer.claims[0].human_label == "Certified"


# -- T4.2: generated platforms inherit the engine -----------------------------


def test_generated_platform_inherits_engine(tmp_path, stub_coder):
    """A freshly generated platform's retrieval entrypoint IS the kernel
    engine — the kernel copytree carries it; there is no local hand-rolled
    retrieval module to diverge."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    blueprint_path = (
        Path(__file__).resolve().parents[3]
        / "blueprints"
        / "examples"
        / "runner_smoke.yaml"
    )
    outcome = RoleRunner(load_blueprint(blueprint_path), tmp_path / "build").run()
    assert outcome.ok, outcome.to_dict()

    engine_file = (
        tmp_path
        / "build"
        / "app"
        / "cerebrum_product_kernel"
        / "retrieval_engine.py"
    )
    assert engine_file.is_file(), "the kit engine must ship in the product kernel"
    src = engine_file.read_text(encoding="utf-8")
    assert "class RetrievalEngine" in src
    assert "keyword_embedder_not_retrieval" in src
