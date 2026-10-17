"""One platform = one branch, forever.

A platform's identity is ``(account, platform_id)``. ``platform_id`` is minted
ONCE, at the platform's first approval, persisted on the session, and never
derived from a name: a session's ``product_id`` is the literal ``"product"``
whenever no vertical was chosen, so names collide by construction.

The branch ``build/<platform_id>`` on cerebrum-builds is the workspace of
record. Its git refs are named here and nowhere else:

* ``build/<platform_id>``                 -- the branch every phase pushes to
* ``archive/<platform_id>/<YYYY-MM-DD>``  -- a head kept before Start over, or
                                             by the 90-day hygiene job
* ``release/<platform_id>/<version>``     -- a certified export's head
"""

from __future__ import annotations

import string
import uuid
from datetime import date
from typing import Any, Optional

PLATFORM_ID_PREFIX = "plt_"
#: ``plt_`` + 16 lowercase hex digits -- checked by its characters, not a pattern.
_PLATFORM_ID_HEX_LEN = 16
_HEX = frozenset(string.hexdigits.lower())
_VERSION_CHARS = frozenset(string.ascii_letters + string.digits + "._-")

BRANCH_PREFIX = "build/"
ARCHIVE_PREFIX = "archive/"
RELEASE_PREFIX = "release/"


class PlatformIdentityError(ValueError):
    """A ref or id that is not a platform's."""


def mint_platform_id() -> str:
    return PLATFORM_ID_PREFIX + uuid.uuid4().hex[:16]


def is_platform_id(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    head, body = text[: len(PLATFORM_ID_PREFIX)], text[len(PLATFORM_ID_PREFIX):]
    return head == PLATFORM_ID_PREFIX and len(body) == _PLATFORM_ID_HEX_LEN and set(body) <= _HEX


def ensure_platform_id(product_design: Any) -> str:
    """The session's platform id, minted on first use and kept forever."""
    current = str(getattr(product_design, "platform_id", None) or "").strip()
    if is_platform_id(current):
        return current
    minted = mint_platform_id()
    product_design.platform_id = minted
    return minted


def platform_id_of(product_design: Any) -> Optional[str]:
    current = str(getattr(product_design, "platform_id", None) or "").strip()
    return current if is_platform_id(current) else None


def _require(platform_id: str) -> str:
    if not is_platform_id(platform_id):
        raise PlatformIdentityError(f"not a platform id: {platform_id!r}")
    return platform_id


def branch_of_record(platform_id: str) -> str:
    return BRANCH_PREFIX + _require(platform_id)


def archive_tag(platform_id: str, day: Optional[date] = None) -> str:
    stamp = (day or date.today()).isoformat()
    return f"{ARCHIVE_PREFIX}{_require(platform_id)}/{stamp}"


def archive_tag_prefix(platform_id: str) -> str:
    return f"{ARCHIVE_PREFIX}{_require(platform_id)}/"


def release_tag(platform_id: str, version: str) -> str:
    ver = str(version or "").strip()
    if not ver or not set(ver) <= _VERSION_CHARS:
        raise PlatformIdentityError(f"not a release version: {version!r}")
    return f"{RELEASE_PREFIX}{_require(platform_id)}/{ver}"


def platform_id_from_branch(branch: str) -> Optional[str]:
    text = str(branch or "")
    if not text.startswith(BRANCH_PREFIX):
        return None
    tail = text[len(BRANCH_PREFIX):]
    return tail if is_platform_id(tail) else None


def recorded_platform_id(output_dir: Any) -> Optional[str]:
    """The platform id a run's ledger records (start_runner_build writes it
    once), or None for a run from before platforms existed."""
    from pathlib import Path

    from app.factory.build.ledger import PLATFORM_ID_KEY, BuildLedger

    path = Path(output_dir) / "build_ledger.jsonl"
    if not path.is_file():
        return None
    try:
        events = list(BuildLedger(path).events())
    except Exception:  # noqa: BLE001 -- an unreadable ledger records nothing
        return None
    for event in reversed(events):
        pid = (getattr(event, "payload", None) or {}).get(PLATFORM_ID_KEY)
        if is_platform_id(pid):
            return str(pid)
    return None
