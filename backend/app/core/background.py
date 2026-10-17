import asyncio
from typing import Dict, Any, Coroutine
from datetime import datetime

_tasks: Dict[str, Dict[str, Any]] = {}


def start_task(task_id: str, coro: Coroutine) -> str:
    """Start a background coroutine and track it under task_id."""
    task = asyncio.create_task(coro)

    _tasks[task_id] = {
        "task": task,
        "started_at": datetime.utcnow(),
        "status": "running",
        "error": None,
    }

    def _done(t):
        info = _tasks.get(task_id)
        if not info:
            return
        if t.exception():
            info["status"] = "failed"
            info["error"] = str(t.exception())
        else:
            info["status"] = "completed"

    task.add_done_callback(_done)
    return task_id


def get_task_status(task_id: str) -> Dict[str, Any]:
    info = _tasks.get(task_id)
    if not info:
        return {"status": "unknown"}
    return {
        "status": info["status"],
        "error": info["error"],
        "started_at": info["started_at"].isoformat(),
    }
