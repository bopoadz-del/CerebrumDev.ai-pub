"""Resolver for the blocks tests may use: DECLARED inventory only.

``blocks.lock.json`` is the declaration: it pins the store repo at a sha and
lists the blocks, and ``backend/app/factory/vendor_blocks_mirror`` is the
vendored copy of exactly that content (kept honest by
``scripts/verify_vendor_parity.py``, which fails closed when the mirror and
the lock disagree).

A sibling ``Cerebrum-Blocks`` checkout is NOT declared inventory. It floats at
whatever sha the developer happens to have, so letting it win meant the same
test asserted different content on CI (no sibling -> mirror) and on a laptop
(sibling -> some other revision). That is why the sibling-backed tests drifted
red locally while passing in CI.

So a real checkout is honoured only when it IS the declared inventory - its
HEAD must equal ``blocks.lock.json``'s ``store.sha``. A drifted checkout is
ignored rather than silently substituted, and callers fall back to the
vendored mirror (or skip, where they need the registry).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT / "blocks.lock.json"


def declared_store_sha() -> str | None:
    """The store sha pinned in blocks.lock.json, or None if unreadable."""
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sha = ((lock.get("store") or {}).get("sha") or "").strip()
    return sha or None


def _head_sha(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def real_blocks_root():
    """The Store checkout these tests may assert against.

    An **explicitly named** checkout (``CEREBRUM_BLOCKS_ROOT`` /
    ``CEREBRUM_BLOCKS_PATH``) is honoured as given. Somebody said which Store
    to test against -- CI says it, and the Factory follows the Store's head, so
    requiring that head to equal ``blocks.lock.json``'s sha made every Store
    move a red pipeline: CI checked the Store out at ``main``, this resolver
    called it undeclared, and the suite reported "no Store checkout at the
    declared pin" while a perfectly good Store sat in ``$RUNNER_TEMP``.

    An **accidental** sibling ``../Cerebrum-Blocks`` still has to match the
    declared sha. That is the case this guard was written for: a developer's
    checkout floating at some other revision, silently changing what the same
    test asserts. Naming it is deliberate; finding it is not.
    """
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    if env:
        named = Path(env)
        return named if (named / "block_registry").is_dir() else None

    sibling = ROOT.parent / "Cerebrum-Blocks"
    if not (sibling / "block_registry").is_dir():
        return None
    declared = declared_store_sha()
    if declared and _head_sha(sibling) != declared:
        # Found, not named, and drifted: ignore rather than test against it.
        return None
    return sibling
