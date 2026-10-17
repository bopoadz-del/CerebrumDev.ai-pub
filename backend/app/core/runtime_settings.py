"""Operator-set runtime settings, changeable from the admin page.

Some settings an operator must be able to change WITHOUT a redeploy or cloud
access — above all the coding model, when the configured one stops responding
(live 2026-10-02: the DeepSeek coder hung and the only lever was an env var on
ECS the operator could not reach). These live in one small JSON file under
STORAGE_PATH and take effect on the next build. An operator override wins over
the environment default; nothing here is a secret (the API KEY still comes from
the deploy env, never from here).

Read on a cold path (once per build), so no cache: the file is tiny and a
build is not hot. Every read and write is wrapped — a missing or unreadable
file is simply "no override", never a crash.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_FILENAME = "runtime_settings.json"

#: Keys an operator may set from the admin page. A value outside this set is
#: refused by the setter, so a typo'd key never lands silently.
CODER_MODEL = "coder_model"
CODER_PROVIDER = "coder_provider"
_ALLOWED_KEYS = frozenset({CODER_MODEL, CODER_PROVIDER})


def _path() -> Path:
    root = os.getenv("STORAGE_PATH", "./storage").strip() or "./storage"
    return Path(root) / _FILENAME


def get_all() -> Dict[str, Any]:
    """Every operator override currently set. Empty dict when none/unreadable."""
    try:
        text = _path().read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k in _ALLOWED_KEYS}
    except (OSError, ValueError):
        pass
    return {}


def get(key: str) -> Optional[str]:
    """One override, or None. A blank override reads as None (not set)."""
    val = get_all().get(key)
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def set_value(key: str, value: Optional[str]) -> Dict[str, Any]:
    """Set (or, with a blank/None value, clear) one override. Returns the new
    full settings dict. Raises ValueError for an unknown key."""
    if key not in _ALLOWED_KEYS:
        raise ValueError(
            f"unknown runtime setting {key!r}; allowed: {', '.join(sorted(_ALLOWED_KEYS))}"
        )
    current = get_all()
    cleaned = str(value or "").strip()
    if cleaned:
        current[key] = cleaned
    else:
        current.pop(key, None)
    _write(current)
    return current


def _write(data: Dict[str, Any]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a reader never sees a half-written file.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".runtime-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except OSError as exc:
        # The admin route surfaces this; a failed write must not look like success.
        raise RuntimeError(f"could not persist runtime settings: {exc}") from exc
