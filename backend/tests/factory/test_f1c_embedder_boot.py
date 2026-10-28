"""F-1c BAR 1: the real semantic embedder, and the engine installed at boot.

* EMBEDDER: the one real provider (``LLM_SEMANTIC_V1``) computes vectors through
  this repo's single LLM egress. The egress is stubbed with an input-derived
  fake (no network): two different texts -> two different vectors; the same text
  twice -> equal vectors. That is the semantic contract, deterministically.
* BOOT INSTALL: ``install_default_engine`` registers a per-tenant engine provider
  (embedder + per-tenant Chroma collection). After boot, ingest-on-parse INDEXES;
  MUTATION — dropping the boot registration leaves the provider absent and the
  "indexes" assertion reddens (the chunks record ``not_indexed: retrieval
  backend not configured``).
"""

from __future__ import annotations

import hashlib
from typing import List

import pytest

import app.core.llm_embeddings as llm_embeddings
from app.cerebrum_product_kernel.ingestion import engine_install, retrieval_index
from app.core.rag_embedding_providers import (
    LLM_SEMANTIC_V1,
    RagEmbeddingProvider,
    get_provider,
    register_provider,
)


# ── the real semantic embedder (egress stubbed, input-derived) ─────────────────


def _fake_vector(text: str) -> List[float]:
    """A deterministic, input-derived fake vector (stands in for the provider)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 for b in digest[:16]]


def test_llm_semantic_provider_is_semantic_and_input_derived(monkeypatch):
    def _stub_request(url, headers, payload, timeout):
        return {"data": [{"embedding": _fake_vector(t)} for t in payload["input"]]}

    monkeypatch.setattr(
        llm_embeddings,
        "get_llm_config",
        lambda: {"base_url": "https://egress.test/v1", "api_key": "k", "model": "embed-model"},
    )
    monkeypatch.setattr(llm_embeddings, "_request_embeddings", _stub_request)

    provider = get_provider(LLM_SEMANTIC_V1)
    assert provider is not None
    assert getattr(provider, "semantic", False) is True
    assert provider.production_approved is True

    # Two DIFFERENT texts -> two DIFFERENT vectors.
    two = provider.embed_texts(["revenue is 100", "sabbatical leave policy"])
    assert len(two) == 2
    assert two[0] != two[1]
    # The SAME text twice -> EQUAL vectors (deterministic).
    same = provider.embed_texts(["revenue is 100", "revenue is 100"])
    assert same[0] == same[1]


def test_embed_texts_refuses_when_egress_unconfigured(monkeypatch):
    """No base_url/key/model -> a surfaced EmbeddingEgressError, never empty."""
    monkeypatch.setattr(
        llm_embeddings, "get_llm_config",
        lambda: {"base_url": "", "api_key": "", "model": ""},
    )
    with pytest.raises(llm_embeddings.EmbeddingEgressError):
        llm_embeddings.embed_texts(["anything"])


# ── the engine installed at host boot ──────────────────────────────────────────


class _DeterministicProvider(RagEmbeddingProvider):
    """A network-free semantic provider for the boot test (RC1 pattern)."""

    semantic = True

    def __init__(self) -> None:
        super().__init__(
            provider_id="test_deterministic_semantic_v1",
            provider_version="1",
            algorithm="test-deterministic",
            dimensions=16,
            distance_metric="cosine",
            normalization="none",
            maximum_batch_size=64,
            maximum_input_characters=200_000,
            production_approved=True,
        )

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [_fake_vector(str(t)) for t in texts]


@pytest.fixture
def boot_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    register_provider(_DeterministicProvider())
    monkeypatch.setenv(engine_install.EMBEDDER_ENV, "test_deterministic_semantic_v1")
    yield
    retrieval_index.set_engine_provider(None)


def test_boot_install_builds_engine_and_indexes(boot_env):
    """After boot, the per-tenant engine is installed and ingest-on-parse INDEXES.

    MUTATION: dropping the boot registration (install_default_engine) leaves the
    provider absent, so this asserts turns red (state becomes not_indexed).
    """
    # Before boot there is no engine (pre-install fallback).
    assert retrieval_index.tenant_engine("tenant-a") is None

    engine_install.install_default_engine()  # the boot registration

    engine = retrieval_index.tenant_engine("tenant-a")
    assert engine is not None
    assert engine.embedder.semantic is True

    chunks = [
        {
            "id": "c1",
            "text": "Revenue is 100. COGS is 60.",
            "layer": 2,
            "tenant_id": "tenant-a",
            "object_id": "obj1",
            "file": "finance.md",
            "location": {"paragraph": 1},
        }
    ]
    outcome = retrieval_index.ingest_parsed_chunks("tenant-a", chunks)
    assert outcome.state == retrieval_index.INDEX_STATE_INDEXED
    assert outcome.index_state == "indexed"
    assert outcome.count == 1

    # The chunk is retrievable from the per-tenant Chroma collection it indexed.
    collection = retrieval_index.tenant_collection("tenant-a")
    hits = engine.retrieve(collection, "Revenue is 100. COGS is 60.")
    assert hits
    assert any(h.file == "finance.md" for h in hits)


def test_no_boot_install_records_surfaced_not_indexed(boot_env):
    """The mutation's expected RED state: with the provider NOT installed,
    ingest-on-parse records a SURFACED not_indexed state, never a silent no-op."""
    # install_default_engine deliberately NOT called here.
    outcome = retrieval_index.ingest_parsed_chunks(
        "tenant-a",
        [{"id": "c1", "text": "x", "layer": 2, "tenant_id": "tenant-a", "object_id": "o"}],
    )
    assert outcome.state == retrieval_index.INDEX_STATE_NOT_INDEXED
    assert retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED in outcome.index_state


# ── M5: the engine is installed via the APP LIFESPAN, not only the direct helper ──


def test_engine_installed_via_app_lifespan(tmp_path, monkeypatch):
    """`with TestClient(app)` runs app.main's lifespan -> install_default_engine
    (main.py). The engine is then LIVE and an ingest INDEXES. Removing the
    lifespan install call reddens this (engine stays None inside the context)."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    register_provider(_DeterministicProvider())
    monkeypatch.setenv(engine_install.EMBEDDER_ENV, "test_deterministic_semantic_v1")
    # Start from a clean slate so only the lifespan can install the engine.
    retrieval_index.set_engine_provider(None)
    assert retrieval_index.tenant_engine("tenant-a") is None

    from fastapi.testclient import TestClient

    from app.main import app

    try:
        with TestClient(app):  # context manager -> lifespan startup runs
            engine = retrieval_index.tenant_engine("tenant-a")
            assert engine is not None, "app lifespan did not install the engine"
            outcome = retrieval_index.ingest_parsed_chunks(
                "tenant-a",
                [{
                    "id": "c1", "text": "Revenue is 100. COGS is 60.", "layer": 2,
                    "tenant_id": "tenant-a", "object_id": "o", "file": "finance.md",
                    "location": {"paragraph": 1},
                }],
            )
            assert outcome.state == retrieval_index.INDEX_STATE_INDEXED
    finally:
        retrieval_index.set_engine_provider(None)


# ── M8: default provider = LLM_SEMANTIC_V1 when the env is unset ────────────────


def test_default_provider_is_llm_semantic_when_env_unset(monkeypatch):
    """A mutant defaulting to 'none' or the hash provider reddens this."""
    monkeypatch.delenv(engine_install.EMBEDDER_ENV, raising=False)
    assert engine_install._default_provider_id() == LLM_SEMANTIC_V1
    embedder = engine_install.resolve_embedder()
    assert embedder is not None
    assert embedder._provider.provider_id == LLM_SEMANTIC_V1
    assert embedder.semantic is True


# ── M9: a NON-SEMANTIC provider named via the env is REFUSED, by NAME ──────────


def test_non_semantic_provider_named_is_refused_by_name(monkeypatch):
    """Naming the (non-semantic) feature-hash provider is refused with the NAMED
    refusal (keyword_embedder_not_retrieval), NOT the generic 'retrieval backend
    not configured'. A mutant that accepts it (or flattens the message) reddens."""
    from app.core.rag_embedding_providers import LOCAL_FEATURE_HASH_V1
    from app.cerebrum_product_kernel.retrieval_engine import RetrievalEngineError

    monkeypatch.setenv(engine_install.EMBEDDER_ENV, LOCAL_FEATURE_HASH_V1)
    # The resolved embedder is present but NOT semantic.
    embedder = engine_install.resolve_embedder()
    assert embedder is not None
    assert embedder.semantic is False
    with pytest.raises(RetrievalEngineError) as exc:
        engine_install.build_tenant_engine("tenant-a")
    assert "keyword_embedder_not_retrieval" in str(exc.value)
    assert retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED not in str(exc.value)


# ── index_state PINNED on the job AND the status payload ───────────────────────


def _parse_md(identity, body):
    from app.cerebrum_product_kernel.ingestion import object_store, parse_job
    from app.cerebrum_product_kernel.ingestion.parsers import parse_object

    stored = object_store.save_object(identity, "finance.md", body)
    job = parse_job.create_job(
        identity, object_id=stored.object_id, source_name=stored.source_name
    )
    final = parse_object(identity, stored.object_id, job_id=job.job_id)
    return final


def test_index_state_pinned_indexed(boot_env):
    """After a parse with a live engine, the job carries index_state='indexed'
    and job_status_payload returns it. Dropping index_state reddens this."""
    from app.cerebrum_product_kernel.ingestion import parse_job
    from app.cerebrum_product_kernel.ingestion.router import job_status_payload

    engine_install.install_default_engine()
    final = _parse_md("tenant-a", b"Liquidity.\n\nRevenue is 100. COGS is 60.\n")
    assert final.status is parse_job.ParseStatus.PARSED
    assert final.index_state == "indexed"
    payload = job_status_payload("tenant-a", final.job_id)
    assert payload["index_state"] == "indexed"


def test_index_state_pinned_not_indexed(boot_env):
    """With no engine, the job carries a SURFACED not_indexed state and the
    payload returns it — never a missing/silent field."""
    from app.cerebrum_product_kernel.ingestion import parse_job
    from app.cerebrum_product_kernel.ingestion.router import job_status_payload

    retrieval_index.set_engine_provider(None)
    final = _parse_md("tenant-a", b"Liquidity.\n\nRevenue is 100. COGS is 60.\n")
    assert final.status is parse_job.ParseStatus.PARSED
    assert final.index_state.startswith("not_indexed")
    assert retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED in final.index_state
    payload = job_status_payload("tenant-a", final.job_id)
    assert payload["index_state"] == final.index_state
