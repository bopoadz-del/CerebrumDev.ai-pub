"""Local product embedder (ORDERS_02NOV_2320Z): BAAI/bge-small-en-v1.5 via
fastembed, the default PRODUCT_RETRIEVAL_EMBEDDER, and the fail-loud probe that
refuses llm_semantic_v1 against a chat provider with no /embeddings.

The embed_texts / _model tests STUB the fastembed model so the vector math
(dimension guard, empty-input branch, L2-normalisation) is exercised
deterministically in CI -- the real weights cannot download in the mutation
sandbox, and a skipped test kills no mutant. Two distinct literal cases per
``==`` assertion so a mutant that flips the verdict is killed.
"""
from __future__ import annotations

import numpy as np
import pytest

import app.core.embeddings_local as el  # dotted token so CI-M associates this file
from app.cerebrum_product_kernel.ingestion.engine_install import (
    EMBEDDER_ENV,
    _default_provider_id,
)
from app.core import rag_embedding_providers as reg


class _StubModel:
    """A fake fastembed embedder: deterministic, distinct per text, configurable
    width so the dimension guard can be exercised."""

    def __init__(self, width: int = el.EMBEDDING_DIM) -> None:
        self._w = width

    def embed(self, items):
        out = []
        for i, text in enumerate(list(items)):
            vec = np.zeros(self._w, dtype=np.float32)
            vec[hash(str(text)) % self._w] = 1.0
            vec[(i + 1) % self._w] += float(i + 1)  # distinct per input
            out.append(vec)
        return out


@pytest.fixture
def stub_384(monkeypatch):
    monkeypatch.setattr(el, "_model", lambda name=el.DEFAULT_EMBEDDING_MODEL: _StubModel(el.EMBEDDING_DIM))


# -- embed_texts: the vector math, stubbed (no model download) -----------------

@pytest.mark.parametrize("a, b", [("alpha", "beta"), ("a contract clause", "a bill of quantities")])
def test_embed_texts_is_384_unit_norm_and_distinguishes(stub_384, a, b):
    vecs = el.embed_texts([a, b])
    assert vecs.shape == (2, el.EMBEDDING_DIM)
    assert vecs.dtype == np.float32
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)
    # Different inputs -> different vectors. Kills embed_texts constant_return.
    assert not np.allclose(vecs[0], vecs[1])


@pytest.mark.parametrize("texts", [[], None])
def test_embed_texts_empty_is_zero_by_dim(texts):
    # Kills drop_rule on the empty-input guard (no model needed: it short-circuits).
    out = el.embed_texts(texts)
    assert out.shape == (0, el.EMBEDDING_DIM)


@pytest.mark.parametrize("bad_width", [100, 768])
def test_embed_texts_wrong_width_raises(monkeypatch, bad_width):
    # Kills swallow_exception: a wrong-width model MUST raise, never a silent store.
    monkeypatch.setattr(el, "_model", lambda name=el.DEFAULT_EMBEDDING_MODEL: _StubModel(bad_width))
    with pytest.raises(RuntimeError):
        el.embed_texts(["x"])


@pytest.mark.parametrize("name", ["model-a", "model-b"])
def test_model_loads_the_named_embedder(monkeypatch, name):
    # Kills _model constant_return: it must construct the NAMED embedder, not a
    # fixed constant. fastembed is patched so no weights are fetched.
    class _Fake:
        def __init__(self, model_name):
            self.model_name = model_name

    monkeypatch.setattr("fastembed.TextEmbedding", _Fake, raising=False)
    el._model.cache_clear()
    try:
        m = el._model(name)
        assert m.model_name == name
    finally:
        el._model.cache_clear()


# -- the real model, best-effort (skips cleanly if weights cannot download) -----

def test_real_fastembed_smoke_optional():
    pytest.importorskip("fastembed")
    el._model.cache_clear()
    try:
        vecs = el.embed_texts(["real embedder smoke"])
    except Exception as exc:  # noqa: BLE001 -- weights unavailable offline; not a failure
        pytest.skip(f"fastembed weights unavailable: {type(exc).__name__}")
    finally:
        el._model.cache_clear()
    assert vecs.shape == (1, el.EMBEDDING_DIM)
    assert np.isclose(float(np.linalg.norm(vecs[0])), 1.0, atol=1e-3)


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
        (RuntimeError("503 Service Unavailable"), False),  # transient -> re-raise original
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
    assert (EMBEDDER_ENV in str(ei.value)) is expect_config_error


@pytest.mark.parametrize(
    "message, expected",
    [("got a 404 Not Found", True), ("401 Unauthorized", False)],
)
def test_looks_like_no_embeddings_endpoint_classifies(message, expected):
    assert reg._looks_like_no_embeddings_endpoint(RuntimeError(message)) is expected
