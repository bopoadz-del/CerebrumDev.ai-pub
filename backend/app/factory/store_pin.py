"""The ONE place the Factory names the Store commit it builds from.

``store.pin`` (repo root, copied to /app in the image) holds a 40-hex Store
sha. CI, the image build and the runtime engine resolution all read it; none
of them follows Store ``main``. ``blocks.lock.json`` is generated from the
Store at exactly this sha, and CI refuses a lock whose ``store.sha`` differs
from the pin or whose block hashes disagree with the pinned Store.

A Store change therefore reaches production only through a Factory PR that
bumps the pin and re-locks in the same commit (``cli bump-store <sha>``) --
one atomic step. Live 2026-10-04: a Store re-sign reached production through
a floating ``main`` while the lock still named the old Store, and every build
died in CLONER on a hash mismatch.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.factory.paths import factory_repo_root

PIN_FILE = "store.pin"
_SHA = re.compile(r"[0-9a-f]{40}")


class StorePinError(ValueError):
    """The pin is missing or is not a full Store commit sha."""


def pin_path(root: Optional[Path] = None) -> Path:
    return Path(root or factory_repo_root()) / PIN_FILE


def read_pin(path: Optional[Path] = None) -> str:
    """The pinned Store sha. Raises when absent or malformed: a Factory that
    cannot say which Store it builds from must not build."""
    target = Path(path) if path else pin_path()
    try:
        sha = target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StorePinError(f"{target}: unreadable ({exc})") from exc
    if not _SHA.fullmatch(sha):
        raise StorePinError(f"{target}: not a 40-hex Store commit sha: {sha[:60]!r}")
    return sha


def pinned_sha_or_none(path: Optional[Path] = None) -> Optional[str]:
    try:
        return read_pin(path)
    except StorePinError:
        return None


def write_pin(sha: str, path: Optional[Path] = None) -> Path:
    sha = (sha or "").strip().lower()
    if not _SHA.fullmatch(sha):
        raise StorePinError(f"not a 40-hex Store commit sha: {sha!r}")
    target = Path(path) if path else pin_path()
    target.write_text(sha + "\n", encoding="utf-8", newline="\n")
    return target
