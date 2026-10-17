"""Deterministic structural text chunking for canonical RAG documents.

No semantic LLM splitting, no embeddings, no summarization. Splits are based on
structural boundaries in priority order.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CHUNKING_VERSION = "structural-character-v1"
DEFAULT_TARGET = 3200
DEFAULT_MAXIMUM = 4000
DEFAULT_OVERLAP = 400
DEFAULT_MINIMUM = 200

# Structural markers.
_HEADING_RE = re.compile(r"^(#{1,6}\s+.*|<h[1-6][^>]*>.*)$", re.MULTILINE | re.IGNORECASE)


@dataclass
class Chunk:
    """A deterministic chunk with positional metadata."""

    text: str
    ordinal: int
    character_start: int
    character_end: int
    overlap_from_previous: int
    structural_type: Optional[str] = None
    heading: Optional[str] = None


@dataclass
class ChunkConfig:
    """Configuration for structural chunking."""

    algorithm: str
    version: str
    target_characters: int
    maximum_characters: int
    overlap_characters: int
    minimum_characters: int

    def validate(self) -> None:
        if self.target_characters <= 0:
            raise ValueError("target_characters must be > 0")
        if self.maximum_characters < self.target_characters:
            raise ValueError("maximum_characters must be >= target_characters")
        if self.overlap_characters < 0:
            raise ValueError("overlap_characters must be >= 0")
        if self.overlap_characters >= self.target_characters:
            raise ValueError("overlap_characters must be < target_characters")
        if self.minimum_characters <= 0 or self.minimum_characters > self.target_characters:
            raise ValueError("minimum_characters must be > 0 and <= target_characters")


def _find_split_point(text: str, max_len: int) -> int:
    """Find the best split point at or before max_len using boundary priority."""
    if len(text) <= max_len:
        return len(text)

    candidate = text[: max_len + 1]

    # 1. Major heading boundary.
    for match in _HEADING_RE.finditer(candidate):
        end = match.end()
        if 0 < end <= max_len:
            return end

    # 2. Blank-line paragraph boundary.
    idx = candidate.rfind("\n\n")
    if idx > 0:
        return idx + 2

    # 3. Single newline.
    idx = candidate.rfind("\n")
    if idx > 0:
        return idx + 1

    # 4. Sentence-ending punctuation followed by whitespace.
    for match in re.finditer(r"[.!?][ \t]+", candidate):
        end = match.end()
        if 0 < end <= max_len:
            return end

    # 5. Word boundary (space).
    idx = candidate.rfind(" ")
    if idx > 0:
        return idx + 1

    # 6. Hard character boundary fallback.
    return max_len


def _find_overlap_start(text: str, overlap_chars: int) -> int:
    """Return the character index where overlap text should begin in *text*."""
    if len(text) <= overlap_chars:
        return 0
    candidate = text[-overlap_chars:]
    # Prefer paragraph boundary.
    idx = candidate.find("\n\n")
    if idx != -1:
        return len(text) - overlap_chars + idx + 2
    # Sentence boundary.
    for match in re.finditer(r"[.!?][ \t]+", candidate):
        start = match.start()
        return len(text) - overlap_chars + start + 2
    # Word boundary.
    idx = candidate.find(" ")
    if idx != -1:
        return len(text) - overlap_chars + idx + 1
    return len(text) - overlap_chars


def chunk_text(
    text: str,
    config: Optional[ChunkConfig] = None,
) -> List[Chunk]:
    """Split canonical text into deterministic structural chunks.

    Returns chunks in source order with stable ordinals and character offsets.
    """
    if config is None:
        config = ChunkConfig(
            algorithm="structural-character",
            version=DEFAULT_CHUNKING_VERSION,
            target_characters=DEFAULT_TARGET,
            maximum_characters=DEFAULT_MAXIMUM,
            overlap_characters=DEFAULT_OVERLAP,
            minimum_characters=DEFAULT_MINIMUM,
        )
    config.validate()

    if not text:
        raise ValueError("CANONICAL_TEXT_EMPTY")

    chunks: List[Chunk] = []
    pos = 0
    ordinal = 0
    prev_overlap_text = ""

    while pos < len(text):
        # Determine the target end for this chunk, including room for overlap.
        target_end = pos + config.target_characters
        if prev_overlap_text:
            target_end -= len(prev_overlap_text)

        target_end = min(target_end, len(text))
        max_end = min(pos + config.maximum_characters - len(prev_overlap_text), len(text))

        # Search for a good split point between target and max.
        split_at = _find_split_point(text[:max_end], target_end)
        if split_at <= pos:
            split_at = max_end
        if split_at <= pos:
            split_at = len(text)

        chunk_text_content = prev_overlap_text + text[pos:split_at]

        # Avoid tiny trailing chunks by merging if safe.
        remaining = len(text) - split_at
        if (
            0 < remaining < config.minimum_characters
            and len(chunk_text_content) + remaining <= config.maximum_characters
        ):
            chunk_text_content += text[split_at:]
            split_at = len(text)

        structural_type = None
        if chunk_text_content.lstrip().startswith("#"):
            structural_type = "heading"
        elif "\n\n" in chunk_text_content:
            structural_type = "paragraphs"
        elif "\n" in chunk_text_content:
            structural_type = "lines"
        else:
            structural_type = "fragment"

        chunk = Chunk(
            text=chunk_text_content,
            ordinal=ordinal,
            character_start=pos,
            character_end=split_at,
            overlap_from_previous=len(prev_overlap_text),
            structural_type=structural_type,
        )
        chunks.append(chunk)

        # Prepare overlap for next chunk.
        if config.overlap_characters > 0 and split_at < len(text):
            overlap_start = _find_overlap_start(text[:split_at], config.overlap_characters)
            prev_overlap_text = text[overlap_start:split_at]
        else:
            prev_overlap_text = ""

        pos = split_at
        ordinal += 1

    return chunks


def chunk_hash(
    document_id: str,
    chunking_version: str,
    ordinal: int,
    character_start: int,
    character_end: int,
    text_hash: str,
) -> str:
    """Return a deterministic chunk identity."""
    data = (
        f"{document_id}:{chunking_version}:{ordinal}:{character_start}:"
        f"{character_end}:{text_hash}"
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
