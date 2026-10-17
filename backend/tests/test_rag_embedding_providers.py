import pytest

from app.core.rag_embedding_providers import (
    LOCAL_FEATURE_HASH_V1,
    get_provider,
)


def test_get_provider_returns_contract():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    assert provider.provider_id == LOCAL_FEATURE_HASH_V1
    assert provider.dimensions == 384
    assert provider.distance_metric == "cosine"
    assert provider.normalization == "l2"
    assert provider.production_approved is False


def test_feature_hash_deterministic():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    v1 = provider.embed_texts(["hello world"])[0]
    v2 = provider.embed_texts(["hello world"])[0]
    assert v1 == v2


def test_feature_hash_l2_normalized():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    vector = provider.embed_texts(["deterministic normalization test"])[0]
    norm = sum(x * x for x in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_feature_hash_empty_text_rejected():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    with pytest.raises(ValueError):
        provider.embed_texts([""])
