"""Construction Knowledge Base loader and retrieval.

Dual-registered from Cerebrum-Blocks ``app/blocks/_knowledge.py``. Ranking
uses the same integer ladder as ``app.core.credibility.CredibilityTier``:
lower int = more credible (CERTIFIED=1 ranks before QUARANTINE=5).
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional

from app.core.credibility import CredibilityTier


_KB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "knowledge", "construction_kb.json"
)
_KB_OVERRIDE_ENV = "CONSTRUCTION_KB_FILE"

_LOCK = threading.RLock()
_KB_CACHE: Optional[Dict[str, Any]] = None
_KB_MTIME: float = 0.0

# Missing/invalid tiers must not outrank CERTIFIED. QUARANTINE is the
# least-credible documented value on the shared ladder.
_MISSING_TIER = int(CredibilityTier.QUARANTINE)


def _kb_path() -> str:
    return os.getenv(_KB_OVERRIDE_ENV) or _KB_PATH


def _load_kb() -> Dict[str, Any]:
    """Reload the KB JSON when its mtime changes."""
    global _KB_CACHE, _KB_MTIME
    path = _kb_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"schema_version": "0", "kb_version": "missing", "entries": []}
    with _LOCK:
        if _KB_CACHE is not None and mtime == _KB_MTIME:
            return _KB_CACHE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "entries" not in data:
                data = {"schema_version": "0", "kb_version": "invalid", "entries": []}
            _KB_CACHE = data
            _KB_MTIME = mtime
        except (OSError, ValueError):
            _KB_CACHE = _KB_CACHE or {
                "schema_version": "0",
                "kb_version": "error",
                "entries": [],
            }
        return _KB_CACHE


def load_knowledge(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return entries; filter by domain (in applicability.applies_to) if given."""
    entries = list(_load_kb().get("entries", []))
    if domain is None:
        return entries
    return [
        e
        for e in entries
        if domain in (e.get("applicability", {}).get("applies_to") or [])
    ]


def get_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    """Return entry by id, or None."""
    for entry in _load_kb().get("entries", []):
        if entry.get("id") == rule_id:
            return entry
    return None


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall((text or "").lower()))


def credibility_rank(entry: Dict[str, Any]) -> int:
    """Sort key for source precedence: lower int ranks first.

    Shares ``CredibilityTier`` (CERTIFIED=1 … QUARANTINE=5). A missing or
    non-int tier sorts as QUARANTINE so it cannot outrank a certified hit.
    """
    tier = entry.get("credibility_tier")
    if isinstance(tier, int):
        return tier
    return _MISSING_TIER


def search_knowledge(
    query: str, top_k: int = 5, domain: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Free-text retrieval over the KB: rank entries by token overlap between
    the query and each entry's id + title + statement, return the top-K.

    Equal relevance is resolved by credibility tier — lower int wins
    (CERTIFIED before QUARANTINE). Empty / no-match query returns [].
    ``domain`` restricts to one applies_to namespace.
    """
    qt = _tokens(query)
    if not qt:
        return []
    scored: List[tuple] = []
    for e in load_knowledge(domain):
        hay = (
            _tokens((e.get("id") or "").replace(".", " "))
            | _tokens(e.get("title"))
            | _tokens(e.get("statement"))
        )
        score = len(qt & hay)
        if score:
            # secondary key: id-token hits weigh a touch more (title relevance)
            id_hits = len(qt & _tokens((e.get("id") or "").replace(".", " ")))
            total = score + 0.5 * id_hits
            # Revision currency: a superseded revision is still citable but
            # must never outrank its successor — down-rank it hard.
            if e.get("superseded_by"):
                total *= 0.25
            scored.append((total, e))
    # Source precedence: equal relevance is resolved by credibility tier —
    # the higher-authority source wins (lower int on the shared ladder).
    scored.sort(key=lambda x: (-x[0], credibility_rank(x[1])))
    return [e for _, e in scored[:top_k]]


def _build_warnings(entry: Dict[str, Any]) -> List[str]:
    """Standard warning list applied to every evaluator response."""
    warnings: List[str] = []
    superseded = entry.get("superseded_by")
    if superseded:
        warnings.append(f"superseded by {superseded} — do not rely on this revision")
    tier = entry.get("credibility_tier")
    applic = entry.get("applicability", {}) or {}
    region = applic.get("region_specific")
    project = applic.get("project_specific")
    if isinstance(tier, int) and tier <= int(CredibilityTier.EXPERIMENTAL):
        warnings.append(
            f"credibility tier {tier}; verify against your project spec or applicable standards"
        )
    if region:
        warnings.append(
            f"region_specific={region}; verify against your project spec or applicable standards"
        )
    if project:
        warnings.append(
            f"project_specific={project}; verify against your project spec or applicable standards"
        )
    return warnings
