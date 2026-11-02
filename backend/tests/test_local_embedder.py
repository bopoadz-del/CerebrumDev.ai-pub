"""Local product embedder (ORDERS_02NOV_2320Z): BAAI/bge-small-en-v1.5 via
fastembed, the default PRODUCT_RETRIEVAL_EMBEDDER, and the fail-loud probe that
refuses llm_semantic_v1 against a chat provider with no /embeddings.

Model-loading tests skip cleanly where fastembed cannot download the weights;
the contract + registry + probe tests always run. Two distinct literal cases
per ``==`` assertion so a mutant that flips the verdict is killed.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.cerebrum_product_kernel.ingestion.engine_install import (
    EMBEDDER_ENV,
    _default_provider_id,
)
from app.core import rag_embedding_providers as reg


# -- the local embedder contract (needs the model; skips if unavailable) ------

@pytest.mark.parametrize("a, b", [("alpha", "beta"), ("a contract clause", "a bill of quantities")])
def test_local_embed_is_384_unit_norm_and_distinguishes(a, b):
    pytest.importorskip("fastembed")
    from app.core import embeddings_local as el

    try:
        vecs = el.embed_texts([a, b])
    except Exception as exc:  # noqa: BLE001 -- model weights unavailable offline
        pytest.skip(f"fastembed model unavailable: {type(exc).__name__}: {exc}")
    assert vecs.shape == (2, el.EMBEDDING_DIM)
    assert vecs.dtype == np.float32
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)
    # Different inputs -> different vectors (a real semantic embedder, not a stub).
    assert not np.allclose(vecs[0], vecs[1])


@pytest.mark.parametrize("texts", [[], None])
def test_local_embed_empty_is_zero_by_dim(texts):
    from app.core import embeddings_local as el

    out = el.embed_texts(texts)
    assert out.shape == (0, el.EMBEDDING_DIM)


# -- the registry + the default (no model load) -------------------------------

@pytest.mark.parametrize(
    "provider_id, expect_present",
    [("local_bge_small_v1", True), ("no_such_provider", False)],
)
def test_registry_has_local_bge(provider_id, expect_present):
    assert (reg.get_provider(provider_id) is not None) is expect_present


@pytest.mark.parametrize(
    "attr, expected",
    [("dimensions", 384), ("semantic", True), ("provider_id", "local_bge_small_v1")],
)
def test_local_bge_provider_contract(attr, expected):
    p = reg.get_provider(reg.LOCAL_BGE_SMALL_V1)
    assert getattr(p, attr) == expected


@pytest.mark.parametrize(
    "env_value, expected",
    [(None, "local_bge_small_v1"), ("local_feature_hash_v1", "local_feature_hash_v1")],
)
def test_default_embedder_is_local_bge_unless_overridden(monkeypatch, env_value, expected):
    if env_value is None:
        monkeypatch.delenv(EMBEDDER_ENV, raising=False)
    else:
        monkeypatch.setenv(EMBEDDER_ENV, env_value)
    assert _default_provider_id() == expected


# -- KILLABLE: llm_semantic_v1 fails LOUD on a provider with no /embeddings ----

@pytest.mark.parametrize(
    "exc, expect_config_error",
    [
        (RuntimeError("Client error '404 Not Found' for url '.../v1/embeddings'"), True),
        (RuntimeError("503 Service Unavailable"), False),  # transient -> re-raise original, not the config error
    ],
)
def test_llm_semantic_probe_raises_clear_error_on_404(monkeypatch, exc, expect_config_error):
    import app.core.llm_embeddings as egress

    def _boom(texts):
        raise exc

    monkeypatch.setattr(egress, "embed_texts", _boom, raising=False)
    provider = reg._LlmSemanticProviderV1()  # fresh so the probe is un-cached
    with pytest.raises(RuntimeError) as ei:
        provider.embed_texts(["x"])
    msg = str(ei.value)
    # On a 404 -> OUR clear config error naming the env; on a transient -> the
    # original error propagates unchanged (never a silent None, never a downgrade).
    assert (EMBEDDER_ENV in msg) is expect_config_error


def test_looks_like_no_embeddings_endpoint_classifies():
    assert reg._looks_like_no_embeddings_endpoint(RuntimeError("got a 404 Not Found")) is True
    assert reg._looks_like_no_embeddings_endpoint(RuntimeError("401 Unauthorized")) is False
