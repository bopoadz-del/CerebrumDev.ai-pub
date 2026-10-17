"""Tests for canonical text normalization."""

from __future__ import annotations

import unicodedata

import pytest

from app.core.rag_canonical_text import canonical_text_hash, normalize_text


def test_normalize_preserves_ascii():
    text = "Hello, world!"
    result, warnings = normalize_text(text)
    assert result == "Hello, world!"
    assert warnings == []


def test_normalize_nfc():
    # e + combining acute -> precomposed é
    text = "e\u0301"
    result, _ = normalize_text(text)
    assert result == "\u00e9"
    assert unicodedata.is_normalized("NFC", result)


def test_normalize_line_endings():
    text = "line1\r\nline2\rline3"
    result, _ = normalize_text(text)
    assert result == "line1\nline2\nline3"


def test_normalize_removes_null_and_controls():
    text = "a\x00b\x01c\x7fd"
    result, warnings = normalize_text(text)
    assert "\x00" not in result
    assert "\x01" not in result
    assert "\x7f" not in result
    assert len(warnings) == 1


def test_normalize_removes_trailing_horizontal_ws():
    text = "line  \t\nother"
    result, _ = normalize_text(text)
    assert result == "line\nother"


def test_normalize_collapses_excessive_blank_lines():
    text = "a\n\n\n\n\nb"
    result, _ = normalize_text(text)
    assert result == "a\n\nb"


def test_normalize_preserves_paragraphs():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    result, _ = normalize_text(text)
    assert "Paragraph one" in result
    assert "Paragraph two" in result
    assert "Paragraph three" in result


def test_normalize_strips_leading_trailing_blank_lines():
    text = "\n\nhello\n\n"
    result, _ = normalize_text(text)
    assert result == "hello"


def test_normalize_does_not_lowercase_or_remove_punctuation():
    text = "Hello, World! This is a TEST."
    result, _ = normalize_text(text)
    assert result == text


def test_canonical_text_hash_stable():
    text = "stable text"
    h1 = canonical_text_hash(text)
    h2 = canonical_text_hash(text)
    assert h1 == h2
    assert len(h1) == 64


def test_unsupported_version_raises():
    with pytest.raises(ValueError):
        normalize_text("text", version="unknown-v99")
