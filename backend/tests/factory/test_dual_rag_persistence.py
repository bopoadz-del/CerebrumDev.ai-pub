"""Factory dual RAG — persistence + embedding fail-closed behaviour."""

from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import types
from pathlib import Path

import pytest


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


ROOT = Path(__file__).resolve().parents[3]
KIT_RAG = (_estate_kit()
    / "rag")


def _load_kit_module(mod_name: str, filename: str, *, deps: dict | None = None):
    """Load a kit rag module under a unique name, wiring fake estate_kit deps."""
    deps = deps or {}
    # Ensure app.estate_kit.rag.* resolves for the kit files' absolute imports.
    pkg_root = "app"
    estate = "app.estate_kit"
    rag = "app.estate_kit.rag"
    for name in (pkg_root, estate, rag):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
            if name == pkg_root:
                sys.modules[name].__path__ = []  # type: ignore[attr-defined]

    for dep_name, dep_mod in deps.items():
        sys.modules[f"app.estate_kit.rag.{dep_name}"] = dep_mod

    path = KIT_RAG / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    # Also register under the import path the file expects when it reloads itself.
    short = filename.replace(".py", "")
    sys.modules[f"app.estate_kit.rag.{short}"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def config_mod(monkeypatch):
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    monkeypatch.delenv("STEWARD_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS", raising=False)
    monkeypatch.delenv("STEWARD_REQUIRE_PERSISTENT_RAG", raising=False)
    monkeypatch.setenv("STEWARD_RAG_PERSISTENCE", "auto")
    # Unique module name per call to avoid stale DualRagConfig defaults
    return _load_kit_module(f"dual_cfg_{id(monkeypatch)}", "config.py")


def test_dual_rag_config_defaults_to_jsonl(config_mod):
    cfg = config_mod.DualRagConfig()
    assert cfg.use_postgres() is False
    assert cfg.embedding_provider_id() == config_mod.LOCAL_FEATURE_HASH_V1
    cfg.validate_or_raise()


def test_require_persistent_rag_fail_closed(config_mod, monkeypatch):
    monkeypatch.setenv("STEWARD_REQUIRE_PERSISTENT_RAG", "1")
    monkeypatch.delenv("STEWARD_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = config_mod.DualRagConfig()
    with pytest.raises(RuntimeError, match="persistent RAG required"):
        cfg.validate_or_raise()


def test_require_production_embeddings_fail_closed(config_mod, monkeypatch):
    monkeypatch.setenv("STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS", "1")
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    cfg = config_mod.DualRagConfig()
    with pytest.raises(RuntimeError, match="production embeddings required"):
        cfg.validate_or_raise()


def test_postgres_mode_selected_when_url_set(config_mod, monkeypatch):
    pw = secrets.token_hex(12)  # generated per test, never a fixed value
    monkeypatch.setenv("STEWARD_DATABASE_URL", f"postgresql://user:{pw}@localhost:5432/steward")
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "fastembed")
    cfg = config_mod.DualRagConfig()
    assert cfg.use_postgres() is True
    assert cfg.embedding_provider_id().startswith("fastembed:")
    assert cfg.normalized_psycopg_url().startswith("postgresql://")
    assert cfg.effective_min_score(0.05) >= 0.62
    assert cfg.effective_min_score(0.8) == 0.8
    cfg.validate_or_raise()


def test_hash_backend_keeps_caller_min_score(config_mod, monkeypatch):
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    cfg = config_mod.DualRagConfig()
    assert cfg.effective_min_score(0.05) == 0.05
    assert cfg.effective_min_score(0.2) == 0.2


def test_jsonl_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    monkeypatch.setenv("STEWARD_RAG_PERSISTENCE", "jsonl")
    monkeypatch.delenv("STEWARD_DATABASE_URL", raising=False)
    cfg = _load_kit_module("dual_cfg_store", "config.py")
    store_mod = _load_kit_module("dual_store", "store.py", deps={"config": cfg})
    store = store_mod.RagIndexStore(tmp_path / "data" / "rag")
    store.upsert_document(
        "steward_sop_v1",
        layer=1,
        doc_id="doc-1",
        new_records=[
            {
                "record_id": "r1",
                "index_id": "steward_sop_v1",
                "layer": 1,
                "doc_id": "doc-1",
                "title": "T",
                "text": "hello",
                "embedding_provider": "local_feature_hash_v1",
                "vector": [0.1] * 384,
                "chunk_ordinal": 0,
            }
        ],
    )
    rows = store.read_records("steward_sop_v1")
    assert len(rows) == 1
    assert rows[0]["doc_id"] == "doc-1"
    stats = store.stats("steward_sop_v1", layer=1)
    assert stats.document_count == 1
    manifest = store.read_manifest("steward_sop_v1")
    assert manifest and manifest["durable"] is False


def test_hash_embedder_deterministic(monkeypatch):
    monkeypatch.setenv("STEWARD_EMBED_BACKEND", "hash")
    monkeypatch.setenv("STEWARD_RAG_PERSISTENCE", "jsonl")
    monkeypatch.delenv("STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS", raising=False)
    cfg = _load_kit_module("dual_cfg_emb", "config.py")
    emb_mod = _load_kit_module("dual_emb", "embeddings.py", deps={"config": cfg})
    emb_mod.reset_embedder()
    emb = emb_mod.get_embedder()
    a = emb.embed("guest arrival checklist")
    b = emb.embed("guest arrival checklist")
    assert a == b
    assert len(a) == 384
    assert emb_mod.cosine_similarity(a, b) > 0.99
    assert emb.provider_id == "local_feature_hash_v1"


def test_oracle_suite_mentions_fastembed_claim():
    suite = json.loads(
        (
            _estate_kit() / "evaluation/steward_live_oracle_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert "fastembed" in suite["embedding_claim"].lower()
    assert len(suite["cases"]) == 10
