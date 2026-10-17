"""Append-only audit trail for L2 heal actions (immutable once written)."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()


class AuditImmutabilityError(RuntimeError):
    """Raised when a caller attempts to rewrite prior audit records."""


def _default_path() -> Path:
    root = Path(os.getenv("STORAGE_PATH", "./storage"))
    return root / "resident_engineer" / "heal_audit.jsonl"


class HealAuditLog:
    """Append-only JSONL audit. Existing lines are never modified in place."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(record)
        payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        line = json.dumps(payload, sort_keys=True) + "\n"
        with _LOCK:
            # Snapshot length to prove we only append.
            before = self.path.stat().st_size
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            after = self.path.stat().st_size
            if after < before + len(line.encode("utf-8")):
                raise AuditImmutabilityError("audit file shrank — refuse")
        return payload

    def read_all(self) -> List[Dict[str, Any]]:
        with _LOCK:
            text = self.path.read_text(encoding="utf-8")
        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def rewrite_forbidden(self, _records: List[Dict[str, Any]]) -> None:
        """Explicitly refuse bulk rewrite attempts (test surface)."""
        raise AuditImmutabilityError("heal audit log is append-only; rewrite forbidden")
