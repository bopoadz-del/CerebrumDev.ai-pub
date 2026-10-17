"""Deterministic canonical text normalization for RAG documents.

Normalization must be stable, versioned, and reversible only in the sense that
re-running the same version on the same input always produces the same output.
No LLM, summarization, or semantic rewriting occurs here.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_NORMALIZATION_VERSION = "canonical-text-v1"

# Prohibited non-printing control characters (excluding tab, LF, CR initially).
# CR and null are handled explicitly; other C0 controls are removed.
_CONTROL_RE = re.compile(r"[\x00\x01-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Horizontal whitespace collapse (spaces and tabs, but not newlines).
_HORIZONTAL_WS_RE = re.compile(r"[ \t]+")

# Excessive blank lines: 3+ consecutive newlines collapse to 2.
_EXCESSIVE_BLANK_RE = re.compile(r"\n{3,}")

# Trailing horizontal whitespace per line.
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def normalize_text(text: str, version: str = DEFAULT_NORMALIZATION_VERSION) -> Tuple[str, List[str]]:
    """Normalize extracted text into a deterministic canonical form.

    Returns a tuple of (normalized_text, warnings).

    Current version: ``canonical-text-v1``
      1. Decode/ensure Unicode (caller provides str).
      2. NFC Unicode normalization.
      3. Normalize CRLF and CR to LF.
      4. Remove null bytes and prohibited control characters.
      5. Remove trailing horizontal whitespace per line.
      6. Collapse repeated horizontal whitespace (spaces/tabs) to a single space.
      7. Collapse 3+ consecutive blank lines to 2.
      8. Remove leading/trailing blank lines and horizontal whitespace.
    """
    warnings: List[str] = []

    if version != DEFAULT_NORMALIZATION_VERSION:
        raise ValueError(f"Unsupported normalization version: {version}")

    if not isinstance(text, str):
        raise TypeError("normalize_text expects a str")

    # 2. Unicode NFC.
    normalized = unicodedata.normalize("NFC", text)

    # 3. Line endings to LF.
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    # 4. Remove null bytes and prohibited controls.
    cleaned = _CONTROL_RE.sub("", normalized)
    if len(cleaned) != len(normalized):
        warnings.append("Removed null bytes and/or prohibited control characters.")

    # 5. Trailing horizontal whitespace per line.
    cleaned = _TRAILING_WS_RE.sub("", cleaned)

    # 6. Collapse horizontal whitespace, but protect newlines.
    # Process line by line so tabs/spaces at line starts/ends are handled safely.
    lines = cleaned.split("\n")
    processed_lines = []
    for line in lines:
        # Collapse internal horizontal whitespace to a single space.
        line = _HORIZONTAL_WS_RE.sub(" ", line)
        line = line.strip(" ")  # remove leading/trailing spaces only
        processed_lines.append(line)
    cleaned = "\n".join(processed_lines)

    # 7. Collapse excessive blank lines.
    cleaned = _EXCESSIVE_BLANK_RE.sub("\n\n", cleaned)

    # 8. Remove leading/trailing blank lines and whitespace.
    cleaned = cleaned.strip("\n").strip()

    return cleaned, warnings


def canonical_text_hash(text: str) -> str:
    """Return the SHA-256 hash of canonical text encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
