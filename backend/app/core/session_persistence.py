"""Persistent JSON snapshot for SessionState.

Keeps a small, atomic, versioned state file on disk so backend restarts do not
lose session progress. chat_history is capped to the most recent
``MAX_PERSISTED_CHAT_MESSAGES`` entries to keep snapshots bounded.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from ..models.session import SessionState

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")
STATE_VERSION = 2
MAX_PERSISTED_CHAT_MESSAGES = 200


def _session_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(session_id: str) -> Path:
    return _session_dir(session_id) / "state.json"


def _backup_path(session_id: str) -> Path:
    return _session_dir(session_id) / "state.json.bak"


def save_session_state(state: SessionState) -> None:
    """Atomically persist a versioned snapshot of *state* to disk.

    Persist chat_history so restarts preserve conversational context.
    chat_history is capped to ``MAX_PERSISTED_CHAT_MESSAGES`` entries. Keeps
    the previous snapshot as ``state.json.bak`` for recovery.
    """
    try:
        snapshot = state.model_dump(mode="json")
        # Bound the snapshot size while keeping the most recent context.
        snapshot["chat_history"] = list(
            state.chat_history[-MAX_PERSISTED_CHAT_MESSAGES:]
        )
        snapshot["version"] = STATE_VERSION

        state_path = _state_path(state.session_id)
        backup_path = _backup_path(state.session_id)
        tmp_path = state_path.with_suffix(".json.tmp")

        # Preserve the previous snapshot as a backup.
        if state_path.exists():
            shutil.copy2(state_path, backup_path)

        tmp_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        os.replace(tmp_path, state_path)
        logger.debug("Saved session state for %s", state.session_id)
    except Exception as exc:
        logger.exception("Failed to save session state for %s: %s", state.session_id, exc)


def load_session_state(session_id: str) -> Optional[SessionState]:
    """Load a session snapshot from disk.

    Supports versioned snapshots. Missing fields fall back to Pydantic defaults.
    Returns ``None`` if no snapshot exists, the version is unsupported, or the
    file is corrupt. Falls back to ``state.json.bak`` if the main file is bad.
    """
    state_path = _state_path(session_id)
    backup_path = _backup_path(session_id)

    candidates = [state_path, backup_path] if backup_path.exists() else [state_path]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = data.get("version", 0)
            if version not in (1, 2):
                logger.warning(
                    "Session %s state file version %s is unsupported; skipping",
                    session_id,
                    version,
                )
                continue
            # Remove version metadata before handing to Pydantic.
            data.pop("version", None)
            return SessionState(**data)
        except Exception as exc:
            logger.warning("Failed to load session state for %s from %s: %s", session_id, path, exc)
            continue

    return None
