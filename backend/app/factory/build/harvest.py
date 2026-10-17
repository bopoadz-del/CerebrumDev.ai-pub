"""S13 upstream harvest — Cerebrum-Blocks / factory-upstream.

STORE_MANAGER's write half was unbuilt (U6) and this module was the honest
BLOCK around it: ``store_write_exists`` was the literal ``False``.

The write path now exists — ``app.factory.build.store_write`` — so this
module reports a real verdict instead of a permanent refusal. Two things
must both hold before harvest is READY:

* **authorization**, a committed ``build/stages/HARVEST_AUTHORIZED.json``
  naming this Store. An environment flag is deliberately not enough: a
  dashboard secret must not be able to write to Cerebrum-Blocks.
* **a writable checkout** — present, clean, and pointing at that Store.

READY means a harvest *may* be written, not that anything was written.
This module still never writes; ``store_write.execute_harvest`` does, onto
a review branch, and it never pushes.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

HARVEST_ID = "app.factory.build.harvest.evaluate_harvest"
BLOCKS_REPO = "bopoadz-del/Cerebrum-Blocks"

# Factory-owned corrections that live only in CerebrumDev.ai emission until
# an authorized harvest exists. Named so the BLOCK is about real files.
# Corrections that still live only in this repo's emission. The F16
# ui_schema divergence has left this list: it is computed and harvestable
# by store_write.plan_harvest. These remain because each needs upstream
# code review, not a mechanical edit.
UNHARVESTED_FIXES = (
    "RoleRunner offline_adapters (Store-unwired vendor contracts)",
    "P1 capture adapter (network:false / llm_provider=none)",
    "S10 versioned Alembic + WAL store (no CREATE TABLE on connect)",
    "S11 fail-closed /health, rollback.sh, JSON request logs",
    "S12 domain_ops / work_queue through execute_action",
)

CONSEQUENCE = (
    "Products will not receive those Factory pipeline fixes from Cerebrum-Blocks "
    "until harvest exists. The next product re-ships Factory-owned corrections "
    "only if CLONER/WRITER emission in this repo stays applied. This does not "
    "pretend Blocks was fixed. No Blocks write was performed; none is authorized "
    "from this repo."
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _store_write_authorized() -> bool:
    """Fail-closed harvest write gate.

    A write would need a committed ``HARVEST_AUTHORIZED.json`` *and* a Store
    write path. STORE_MANAGER's write half is unbuilt (U6). Environment
    flags are not authorization — a dashboard secret must not push to
    Cerebrum-Blocks. This is a policy no-op, not a stub that pretends to
    authorize.
    """
    root = _repo_root()
    marker = root / "build" / "stages" / "HARVEST_AUTHORIZED.json"
    has_marker = False
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        has_marker = (
            data.get("authorized") is True and data.get("blocks_repo") == BLOCKS_REPO
        )
    # U6: a write path now exists (app.factory.build.store_write). It writes
    # to a fresh branch in a Cerebrum-Blocks checkout and never pushes, so
    # authorization still gates it: the marker says this repo may write, the
    # capability says a write is mechanically possible, and both are needed.
    from app.factory.build.store_write import store_write_capability

    store_write_exists = bool(store_write_capability().get("implemented"))
    return bool(has_marker and store_write_exists)


def _blocks_checkout(explicit: Optional[Path] = None) -> Optional[Path]:
    if explicit is not None:
        path = Path(explicit)
        return path if path.exists() else None
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    if env:
        path = Path(env)
        if path.exists():
            return path
    sibling = _repo_root().parent / "Cerebrum-Blocks"
    if sibling.exists():
        return sibling
    return None


def evaluate_harvest(blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return a harvest verdict. Never copies files. Never writes upstream."""
    from app.factory.build.store_write import checkout_is_writable

    checkout = _blocks_checkout(blocks_root)
    authorized = _store_write_authorized()
    copied: list[str] = []
    writable = checkout_is_writable(checkout)

    if authorized and writable["writable"]:
        blocked = False
        reason = (
            "harvest is authorized and a writable Cerebrum-Blocks checkout is "
            f"present at {checkout}. Run store_write.execute_harvest to write a "
            "review branch; it never targets the default branch and never pushes."
        )
    else:
        blocked = True
        if not authorized:
            reason = (
                "harvest write is NOT authorized. A committed "
                "build/stages/HARVEST_AUTHORIZED.json naming "
                f"{BLOCKS_REPO} is required; an environment flag is not "
                "authorization. The write path itself now exists (U6, "
                "app.factory.build.store_write)."
            )
        else:
            reason = (
                "harvest is authorized but no writable checkout is available: "
                f"{writable['reason']}."
            )
    return {
        "gate": HARVEST_ID,
        "verdict": "BLOCKED" if blocked else "READY",
        "ok": not blocked,
        "blocked": blocked,
        "copied": copied,
        "copied_count": 0,
        "authorized_write_path": authorized,
        "blocks_repo": BLOCKS_REPO,
        "blocks_checkout": str(checkout) if checkout else None,
        "checkout_writable": writable["writable"],
        "write_path": "app.factory.build.store_write",
        "unharvested_fixes": list(UNHARVESTED_FIXES),
        "reason": reason,
        "consequence": CONSEQUENCE,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
