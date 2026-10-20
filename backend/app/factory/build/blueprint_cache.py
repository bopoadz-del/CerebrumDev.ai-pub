"""Brief-keyed blueprint cache (Item 2 keystone).

The architect re-drafts a DIFFERENT blueprint for the same brief on every run
(the draft is an LLM call), so a fresh re-run has no stable identity to resume
against -- the whole product is rebuilt from scratch. Cache the drafted
blueprint keyed by the brief, the vertical and the Factory code version, so a
re-run of an unchanged brief REUSES the prior blueprint instead of drafting a
new one. A stable blueprint is what makes every downstream stage (clone,
write, test) reproducible and resumable.

The key is (brief + vertical + Factory git sha). The Store pin is deliberately
NOT in it: the blueprint is drafted before anything is cloned, so it does not
depend on the Store -- that dependency lives in the downstream stage keys
(runner.full_input_hash). A new Factory deploy changes the git sha and so
invalidates cached blueprints, exactly matching "resume only on an unchanged
Factory".

Gated by FACTORY_STAGE_RESUME (default off): with the flag unset the cache is
never read or written, so the live default is unchanged until it is enabled
after a validating build. General: keyed only on (brief, vertical, code
version); never a product name, a session id, or a brief's identity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

#: env names the Factory uses for its own git sha (see app/main.py _git_sha)
_SHA_ENV = ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION")


def stage_resume_on(env: Optional[Dict[str, str]] = None) -> bool:
    """FACTORY_STAGE_RESUME gates the cross-session blueprint cache / resume.
    Off by default so carrying the code changes nothing live until enabled."""
    src = env if env is not None else os.environ
    return str(src.get("FACTORY_STAGE_RESUME", "")).strip().lower() in (
        "1", "on", "true", "yes",
    )


def factory_code_version(env: Optional[Dict[str, str]] = None) -> str:
    """The Factory's deployed git sha (same source as the /version route).
    Empty when unset -- the cache still works, it just does not vary by code
    version in a bare dev run."""
    src = env if env is not None else os.environ
    for key in _SHA_ENV:
        val = str(src.get(key) or "").strip()
        if val:
            return val
    return ""


def brief_blueprint_key(brief: str, vertical: str = "", factory_sha: str = "") -> str:
    """Stable key for a drafted blueprint: brief + vertical + Factory code
    version. Whitespace around the brief is normalized so a re-paste that
    differs only in trailing newlines still resumes."""
    payload = json.dumps(
        {
            "brief": (brief or "").strip(),
            "vertical": (vertical or "").strip(),
            "factory": (factory_sha or "").strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_dir() -> Path:
    from app.factory.paths import factory_outputs_root

    return factory_outputs_root() / "blueprint_cache"


def load_blueprint(key: str) -> Optional[Dict[str, Any]]:
    """The cached blueprint dict for this key, or None. Any read error is a
    miss -- a cache is an optimisation, never a hard dependency."""
    try:
        path = _cache_dir() / f"{key}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        bp = data.get("blueprint") if isinstance(data, dict) else None
        return bp if isinstance(bp, dict) else None
    except Exception:  # noqa: BLE001 -- a torn cache entry is just a miss
        return None


def store_blueprint(key: str, blueprint: Dict[str, Any]) -> bool:
    """Persist the drafted blueprint under its key. Best-effort: a write
    failure is swallowed (the draft still proceeds). Returns True on write."""
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.json.tmp"
        tmp.write_text(
            json.dumps({"key": key, "blueprint": blueprint}, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(d / f"{key}.json")
        return True
    except Exception:  # noqa: BLE001 -- caching must never break a draft
        return False
