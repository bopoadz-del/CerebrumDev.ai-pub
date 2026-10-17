"""Persisted work queue processed through execute_action.

Enqueue writes a pending row. Process runs the capability through the
vendored kernel and records processed|failed plus the ActionResult.
A handler that returns ok without changing status is a hollow queue (F5).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.store import connect

TABLE = 'work_queue'
IDEMPOTENCY = 'idempotency'
PENDING = "pending"
PROCESSING = "processing"
PROCESSED = "processed"
FAILED = "failed"


def enqueue(
    capability_id: str,
    payload: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    conn = connect()
    try:
        cur = conn.execute(
            f"INSERT INTO {TABLE} (capability_id, payload, status, result, "
            "idempotency_key) VALUES (?, ?, ?, ?, ?)",
            (
                capability_id,
                json.dumps(payload, sort_keys=True),
                PENDING,
                None,
                idempotency_key,
            ),
        )
        conn.commit()
        item_id = int(cur.lastrowid)
    finally:
        conn.close()
    item = get(item_id)
    if item is None:
        raise RuntimeError("work_queue enqueue did not persist")
    return item


def get(item_id: int) -> Dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE id = ?", (item_id,)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def list_all() -> List[Dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(f"SELECT * FROM {TABLE} ORDER BY id").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def claim_pending(item_id: int) -> Dict[str, Any] | None:
    conn = connect()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET status = ? WHERE id = ? AND status = ?",
            (PROCESSING, item_id, PENDING),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    return get(item_id)


def mark(
    item_id: int,
    status: str,
    result: Dict[str, Any] | None,
    *,
    from_status: str | None = None,
) -> Dict[str, Any] | None:
    conn = connect()
    try:
        if from_status is None:
            cur = conn.execute(
                f"UPDATE {TABLE} SET status = ?, result = ? WHERE id = ?",
                (
                    status,
                    json.dumps(result, sort_keys=True) if result is not None else None,
                    item_id,
                ),
            )
        else:
            cur = conn.execute(
                f"UPDATE {TABLE} SET status = ?, result = ? WHERE id = ? AND status = ?",
                (
                    status,
                    json.dumps(result, sort_keys=True) if result is not None else None,
                    item_id,
                    from_status,
                ),
            )
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    return get(item_id)


def recall(key: str) -> Dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {IDEMPOTENCY} WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def remember(key: str, entity: str, record_id: int) -> None:
    conn = connect()
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO {IDEMPOTENCY} (key, entity, record_id) "
            "VALUES (?, ?, ?)",
            (key, entity, record_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    raw_payload = item.get("payload")
    if isinstance(raw_payload, str):
        try:
            item["payload"] = json.loads(raw_payload)
        except json.JSONDecodeError:
            pass
    raw_result = item.get("result")
    if isinstance(raw_result, str) and raw_result:
        try:
            item["result"] = json.loads(raw_result)
        except json.JSONDecodeError:
            pass
    return item
