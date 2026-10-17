"""F5: sanitisation for anything rendered into logs or build-status.

Hostnames and short opaque tokens (deploy ids, session ids, receipt ids)
are allowed through — an operator needs them to name what they are looking
at. API keys and auth headers are never allowed: they are redacted to a
stable token so the failure text stays readable without leaking credentials
into the ledger, the service log, or the Floor.

The redaction patterns are DATA (app/factory/security/secret_patterns.json):
provider key formats, credential headers and assignments, the long-blob
shape and an entropy threshold. They are loaded at run time and used only to
substitute ``[redacted]`` -- nothing here branches on a match. When the data
cannot be read the scrubber fails closed: every token-shaped run of
``_FALLBACK_MIN_LENGTH`` or more characters is redacted.
"""

import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

PATTERNS_PATH = Path(__file__).resolve().parents[1] / "security" / "secret_patterns.json"

_REDACTED = "[redacted]"

#: Fail-closed floor when the data file is unreadable: redact more, never less.
_FALLBACK_MIN_LENGTH = 16
_FALLBACK_TOKEN = r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{16,}(?![A-Za-z0-9+/_=-])"


def _entropy(text: str) -> float:
    """Shannon entropy in bits per character."""
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


@lru_cache(maxsize=1)
def _rules() -> Dict[str, Any]:
    try:
        data = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
        rules = {
            "header_line": re.compile(data["header_line"]),
            "key_assignment": re.compile(data["key_assignment"]),
            "provider_keys": [re.compile(p) for p in data["provider_keys"]],
            "long_blob": re.compile(data["long_blob"]),
            "token": re.compile(data["token"]),
            "min_length": int(data["entropy"]["min_length"]),
            "bits": float(data["entropy"]["bits_per_char"]),
            "loaded": True,
        }
        return rules
    except (OSError, ValueError, KeyError, TypeError, re.error):
        return {
            "header_line": None,
            "key_assignment": None,
            "provider_keys": [],
            "long_blob": None,
            "token": re.compile(_FALLBACK_TOKEN),
            "min_length": _FALLBACK_MIN_LENGTH,
            "bits": 0.0,
            "loaded": False,
        }


def _redact_value(m: "re.Match[str]") -> str:
    # Keep the key name so "DEEPSEEK_API_KEY=[redacted]" is still readable;
    # drop the value and anything after it on the line.
    return m.group(0)[: m.start(1) - m.start(0)] + _REDACTED


def _redact_by_entropy(min_length: int, bits: float):
    def _sub(m: "re.Match[str]") -> str:
        token = m.group(0)
        too_random = len(token) >= min_length and _entropy(token) >= bits
        return _REDACTED if too_random else token

    return _sub


def sanitize_for_status(text: Optional[str]) -> str:
    """Return *text* with keys and auth headers redacted, tokens kept.

    A no-op for non-string input so callers can pass ``None`` through
    without a guard.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    rules = _rules()
    out = text
    substitutions: List[Any] = [
        (rules["header_line"], _REDACTED),
        (rules["key_assignment"], _redact_value),
        *((p, _REDACTED) for p in rules["provider_keys"]),
        (rules["long_blob"], _REDACTED),
        (rules["token"], _redact_by_entropy(rules["min_length"], rules["bits"])),
    ]
    for pattern, replacement in substitutions:
        if pattern is not None:
            out = pattern.sub(replacement, out)
    return out
