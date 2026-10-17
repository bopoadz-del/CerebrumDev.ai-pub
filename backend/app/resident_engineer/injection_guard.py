"""Treat DNA / catalog / log text as untrusted data (never execute)."""

from __future__ import annotations

import re
from typing import Any

# Instruction-like patterns commonly used in prompt-injection payloads.
_INSTRUCTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
        r"disregard\s+(all\s+)?(previous|prior|above)",
        r"system\s*:\s*",
        r"<\s*/?\s*system\s*>",
        r"you\s+are\s+now\s+",
        r"act\s+as\s+(if\s+)?(you\s+are\s+)?",
        r"execute\s+(the\s+)?(following\s+)?(shell|code|command)",
        r"run\s+`[^`]+`",
        r"os\.system\s*\(",
        r"subprocess\.",
        r"__import__\s*\(",
        r"eval\s*\(",
        r"exec\s*\(",
    )
]

_REPLACEMENT = "[untrusted-content-stripped]"


def strip_instruction_patterns(text: str) -> str:
    """Remove instruction-like spans from untrusted text."""
    if not text:
        return text
    cleaned = text
    for pat in _INSTRUCTION_PATTERNS:
        cleaned = pat.sub(_REPLACEMENT, cleaned)
    return cleaned


def sanitize_untrusted(value: Any) -> Any:
    """Recursively sanitize strings in JSON-like structures."""
    if isinstance(value, str):
        return strip_instruction_patterns(value)
    if isinstance(value, list):
        return [sanitize_untrusted(v) for v in value]
    if isinstance(value, dict):
        return {str(k): sanitize_untrusted(v) for k, v in value.items()}
    return value
