"""Isolated workspace *record* for a change-request (no agent, no worktree spawn).

M3 only records where a future M4 worktree WOULD be created.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def create_workspace_record(
    request_id: str,
    product_id: str,
    *,
    product_root: str | None = None,
) -> Dict[str, Any]:
    root = Path(os.getenv("STORAGE_PATH", "./storage")) / "change_requests" / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    path = root / request_id
    path.mkdir(parents=True, exist_ok=True)
    meta = {
        "request_id": request_id,
        "product_id": product_id,
        "path": str(path),
        "product_root": product_root,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "worktree_created": False,
        "agent_started": False,
        "honesty": "M3 dry-run record only — M4 creates isolated worktrees/agents",
    }
    (path / "workspace.json").write_text(
        __import__("json").dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return meta
