"""Persistent change-request queue — Redis if REDIS_URL set, else disk JSON.

M3 states: received → validated → dry_run_evaluated → awaiting_approval → approved|rejected
M4 states (after approved): workbench_running → candidate_ready → gate_passed|gate_failed → staged

Approval alone still executes nothing; M4 workbench owns candidate build / gates / packager staging.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ItemField(str, Enum):
    """Typed keys of a queue item; the stored key is the member name."""

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: list) -> str:
        return name

    audit_trail = auto()  # append-only list of audit entries

QUEUE_STATES = (
    "received",
    "validated",
    "dry_run_evaluated",
    "awaiting_approval",
    "approved",
    "rejected",
    # M4 Build Mode workbench
    "workbench_running",
    "candidate_ready",
    "gate_passed",
    "gate_failed",
    "staged",
)

M4_QUEUE_STATES = (
    "workbench_running",
    "candidate_ready",
    "gate_passed",
    "gate_failed",
    "staged",
)

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue_root() -> Path:
    root = Path(os.getenv("STORAGE_PATH", "./storage")) / "change_requests" / "queue"
    root.mkdir(parents=True, exist_ok=True)
    return root


def rejection_log_path() -> Path:
    path = Path(os.getenv("STORAGE_PATH", "./storage")) / "change_requests" / "rejections.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


def log_rejection(reason: str, payload: Any = None) -> None:
    line = json.dumps(
        {"recorded_at": _now(), "reason": reason, "payload": payload},
        sort_keys=True,
    )
    with _LOCK:
        with rejection_log_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class ChangeRequestQueue:
    """Disk-backed queue with optional Redis index for multi-worker visibility."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else queue_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._redis = self._connect_redis()

    def _connect_redis(self):
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            return None
        try:
            import redis  # type: ignore

            client = redis.from_url(url, socket_connect_timeout=1)
            client.ping()
            return client
        except Exception as exc:  # noqa: BLE001
            logger.warning("REDIS_URL set but unavailable (%s); using disk queue", exc)
            return None

    def _item_path(self, request_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request_id)
        return self.root / f"{safe}.json"

    def _atomic_write(self, path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        path = self._item_path(request_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put_received(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Idempotent insert: same request_id returns existing item."""
        request_id = str(request["request_id"])
        existing = self.get(request_id)
        if existing:
            return existing
        item = {
            "request_id": request_id,
            "state": "received",
            "request": request,
            "created_at": _now(),
            "updated_at": _now(),
            "validation_errors": [],
            "dry_run_report": None,
            "workspace": None,
            "decision": None,
            "history": [{"state": "received", "at": _now()}],
        }
        with _LOCK:
            self._atomic_write(self._item_path(request_id), item)
            self._redis_index(item)
        return item

    def update_state(
        self,
        request_id: str,
        state: str,
        *,
        validation_errors: Optional[List[str]] = None,
        dry_run_report: Optional[Dict[str, Any]] = None,
        workspace: Optional[Dict[str, Any]] = None,
        decision: Optional[Dict[str, Any]] = None,
        workbench_session: Optional[Dict[str, Any]] = None,
        candidate: Optional[Dict[str, Any]] = None,
        gate_report: Optional[Dict[str, Any]] = None,
        promotion: Optional[Dict[str, Any]] = None,
        audit_append: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if state not in QUEUE_STATES:
            raise ValueError(f"invalid queue state: {state}")
        with _LOCK:
            item = self.get(request_id)
            if not item:
                raise KeyError(request_id)
            item["state"] = state
            item["updated_at"] = _now()
            item.setdefault("history", []).append({"state": state, "at": item["updated_at"]})
            if validation_errors is not None:
                item["validation_errors"] = validation_errors
            if dry_run_report is not None:
                item["dry_run_report"] = dry_run_report
            if workspace is not None:
                item["workspace"] = workspace
            if decision is not None:
                item["decision"] = decision
            if workbench_session is not None:
                item["workbench_session"] = workbench_session
            if candidate is not None:
                item["candidate"] = candidate
            if gate_report is not None:
                item["gate_report"] = gate_report
            if promotion is not None:
                item["promotion"] = promotion
            if audit_append is not None:
                trail = item.setdefault(ItemField.audit_trail.value, [])
                if not isinstance(trail, list):
                    trail = []
                    item[ItemField.audit_trail.value] = trail
                entry = dict(audit_append)
                entry.setdefault("at", item["updated_at"])
                trail.append(entry)
            self._atomic_write(self._item_path(request_id), item)
            self._redis_index(item)
            return item

    def list_items(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if state and data.get("state") != state:
                continue
            items.append(data)
        return items

    def record_decision(self, request_id: str, *, approved: bool, actor: str, note: str = "") -> Dict[str, Any]:
        """Record approval/rejection — does NOT execute anything (M4 owns execution)."""
        state = "approved" if approved else "rejected"
        return self.update_state(
            request_id,
            state,
            decision={
                "approved": approved,
                "actor": actor,
                "note": note,
                "recorded_at": _now(),
                "executes": False,
                "honesty": "decision recorded only; no agent/workbench execution in M3",
            },
        )

    def _redis_index(self, item: Dict[str, Any]) -> None:
        if not self._redis:
            return
        try:
            key = f"change_requests:{item['request_id']}"
            self._redis.set(key, json.dumps({"state": item["state"], "updated_at": item["updated_at"]}))
            self._redis.sadd("change_requests:ids", item["request_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis index update failed: %s", exc)
