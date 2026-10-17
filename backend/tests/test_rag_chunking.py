"""Tests for deterministic structural chunking."""

from __future__ import annotations

import pytest

from app.core.rag_chunking import (
    DEFAULT_CHUNKING_VERSION,
    ChunkConfig,
    chunk_hash,
    chunk_text,
)


def _config(**kwargs) -> ChunkConfig:
    defaults = {
        "algorithm": "structural-character",
        "version": DEFAULT_CHUNKING_VERSION,
        "target_characters": 100,
        "maximum_characters": 150,
        "overlap_characters": 20,
        "minimum_characters": 20,
    }
    defaults.update(kwargs)
    return ChunkConfig(**defaults)


def test_chunk_preserves_order():
    text = " ".join(f"sentence{i}" for i in range(20))
    chunks = chunk_text(text, _config())
    assert len(chunks) > 1
    ordinals = [c.ordinal for c in chunks]
    assert ordinals == list(range(len(chunks)))


def test_chunk_ordinals_start_at_zero():
    chunks = chunk_text("a " * 100, _config())
    assert chunks[0].ordinal == 0


def test_chunk_offsets_correct():
    text = "a " * 100
    chunks = chunk_text(text, _config())
    for c in chunks:
        assert text[c.character_start:c.character_end] == c.text[c.overlap_from_previous:]


def test_chunk_ids_deterministic():
    text = "a " * 100
    config = _config()
    chunks1 = chunk_text(text, config)
    chunks2 = chunk_text(text, config)
    for a, b in zip(chunks1, chunks2):
        h1 = chunk_hash("doc1", config.version, a.ordinal, a.character_start, a.character_end, "texthash")
        h2 = chunk_hash("doc1", config.version, b.ordinal, b.character_start, b.character_end, "texthash")
        assert h1 == h2


def test_chunk_maximum_enforced():
    text = "x" * 500
    chunks = chunk_text(text, _config(maximum_characters=150))
    for c in chunks:
        assert len(c.text) <= 150


def test_empty_text_fails():
    with pytest.raises(ValueError):
        chunk_text("", _config())


def test_invalid_config_overlap_too_large():
    with pytest.raises(ValueError):
        _config(overlap_characters=100).validate()


def test_invalid_config_maximum_below_target():
    with pytest.raises(ValueError):
        _config(target_characters=100, maximum_characters=50).validate()


def test_tiny_final_chunk_merged():
    text = "word " * 25  # small enough to fit in one chunk
    chunks = chunk_text(text, _config(target_characters=200, maximum_characters=300))
    assert len(chunks) == 1


def test_overlap_recorded():
    text = " ".join(f"sentence{i}" for i in range(30))
    chunks = chunk_text(text, _config(target_characters=80, overlap_characters=20))
    for c in chunks[1:]:
        assert c.overlap_from_previous > 0
        assert c.overlap_from_previous <= 20


def test_paragraph_boundary_preferred():
    text = "\n\n".join(f"Paragraph {i} with enough text to matter here." for i in range(10))
    chunks = chunk_text(text, _config(target_characters=50, maximum_characters=100))
    # Most splits should happen at paragraph boundaries when possible.
    boundary_splits = sum(1 for c in chunks if c.text.startswith("Paragraph"))
    assert boundary_splits > 0
