"""Resolve Factory repo root across local and Docker layouts.

Local checkout::
  <repo>/backend/app/factory/*.py  → root = <repo>

Docker image (Dockerfile copies ``backend/app`` → ``/app/app``)::
  /app/app/factory/*.py            → root = /app
  with ``blueprints/`` copied to ``/app/blueprints``.
  S0 inventory also plants ``/app/backend/app`` → ``/app/app`` and
  ``/app/.github/workflows/ci.yml`` so repo-relative FACTORY_SOURCE_PATHS exist.
"""

from __future__ import annotations

import os
from pathlib import Path


def factory_repo_root(anchor: Path | None = None) -> Path:
    """Return the directory that contains ``blueprints/`` (and factory_outputs)."""
    here = (anchor or Path(__file__)).resolve()
    for root in (here.parents[3], here.parents[2], Path("/app"), Path.cwd()):
        if (root / "blueprints").is_dir():
            return root
    # Prefer local-dev layout when blueprints are absent (tests may monkeypatch)
    return here.parents[3]


class UnsafeOutputDir(ValueError):
    """A requested generation target resolves outside the outputs root."""


def factory_outputs_root() -> Path:
    """The only directory tree generation is allowed to create or destroy.

    Resolution order:

    1. ``FACTORY_OUTPUTS_ROOT`` — explicit operator override.
    2. ``$STORAGE_PATH/factory_outputs`` — production. The repo root lives on
       the ephemeral container filesystem; only ``STORAGE_PATH`` is a mounted
       persistent disk. Generated platforms are the product's deliverable, so
       they must survive a deploy — before this, every generation was wiped by
       the next release and the download endpoint answered 404
       ("generate again"), burning the customer's metered quota.
    3. ``<repo>/factory_outputs`` — local checkouts and tests, unchanged.

    Production entrypoint also symlinks ``/app/factory_outputs`` →
    ``$STORAGE_PATH/factory_outputs`` so session-baked absolute paths under
    the legacy location remain on the Render disk across deploys.
    """
    explicit = os.getenv("FACTORY_OUTPUTS_ROOT", "").strip()
    if explicit:
        return Path(explicit)
    storage = os.getenv("STORAGE_PATH", "").strip()
    if storage:
        return Path(storage) / "factory_outputs"
    # Docker layout without env (should not happen on Render): prefer the
    # conventional mount so a missing STORAGE_PATH cannot silently write to
    # ephemeral /app/factory_outputs.
    if Path("/app/storage").is_dir() and (Path("/app/blueprints").is_dir() or Path("/app/app").is_dir()):
        return Path("/app/storage/factory_outputs")
    return factory_repo_root() / "factory_outputs"


def is_within_outputs_root(candidate: Path | str) -> bool:
    """Whether ``candidate`` resolves inside :func:`factory_outputs_root`."""
    root = factory_outputs_root().resolve()
    try:
        resolved = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def is_safe_to_clean(candidate: Path | str) -> bool:
    """Whether ``candidate`` may be recursively deleted by the generator.

    Deliberately wider than :func:`is_within_outputs_root`. ``ProductGenerator``
    is a library: the CLI and the test suite legitimately generate into
    temporary directories, and confining it to ``factory_outputs/`` would break
    them. What it must never do is delete somewhere real -- ``/app``,
    ``/app/storage``, a home directory, ``/``.

    The strict check stays where untrusted input actually arrives, on the HTTP
    boundary in :func:`safe_output_dir`. This is the second line, sized so that
    it refuses catastrophe without dictating where trusted callers may work.
    """
    import tempfile

    if is_within_outputs_root(candidate):
        return True
    try:
        resolved = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return False
    if resolved == Path(resolved.anchor):  # a filesystem root
        return False
    tmp_root = Path(tempfile.gettempdir()).resolve()
    return tmp_root in resolved.parents


def safe_output_dir(candidate: Path | str | None, product_id: str) -> Path:
    """Resolve a generation target, refusing anything outside the outputs root.

    ``ProductGenerator.generate`` calls ``shutil.rmtree`` on this path before
    writing, so an unvalidated value is a recursive-delete primitive: a caller
    passing ``/app/storage`` would destroy the accounts database, every session
    and every stored upload. The target is therefore confined to
    ``factory_outputs/``, and traversal is caught by resolving first and then
    checking containment (``..`` segments and symlinks both collapse under
    ``Path.resolve``).
    """
    root = factory_outputs_root()
    if candidate is None or str(candidate).strip() == "":
        return root / product_id
    if not is_within_outputs_root(candidate):
        raise UnsafeOutputDir(
            f"output_dir must resolve inside {root}; refusing {candidate!r}"
        )
    return Path(candidate).resolve()


class UnsafeProductRoot(ValueError):
    """A workbench product_root resolved outside factory_outputs/."""


def safe_workbench_product_root(candidate: Path | str) -> Path:
    """Resolve a workbench workspace, refusing anything outside factory_outputs/.

    Same containment rule as :func:`safe_output_dir`. Workbench is default-off;
    when the flag is on, a caller-supplied path must still stay inside the
    outputs tree.
    """
    if candidate is None or str(candidate).strip() == "":
        raise UnsafeProductRoot("product_root is required")
    resolved = Path(candidate).resolve()
    if not is_within_outputs_root(resolved):
        raise UnsafeProductRoot(
            f"product_root must resolve inside {factory_outputs_root()}; "
            f"refusing {candidate!r}"
        )
    return resolved


def cleanup_stale_session_outputs(*, max_age_days: int | None = None) -> dict:
    """Delete old ``factory_outputs/sessions/*`` trees.

    Hook for the in-process backup scheduler. Does not resize disks.
    ``FACTORY_OUTPUTS_MAX_AGE_DAYS`` (default 14) is the retention.
    """
    import shutil
    import time

    if max_age_days is None:
        raw = os.getenv("FACTORY_OUTPUTS_MAX_AGE_DAYS", "14").strip()
        try:
            max_age_days = max(1, int(raw))
        except ValueError:
            max_age_days = 14
    root = factory_outputs_root() / "sessions"
    removed = 0
    bytes_freed = 0
    cutoff = time.time() - (max_age_days * 86400)
    if not root.is_dir():
        return {
            "ok": True,
            "removed": 0,
            "bytes_freed": 0,
            "max_age_days": max_age_days,
        }
    for session_dir in list(root.iterdir()):
        if not session_dir.is_dir():
            continue
        try:
            mtime = session_dir.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        if not is_safe_to_clean(session_dir):
            continue
        try:
            size = sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())
            shutil.rmtree(session_dir)
            removed += 1
            bytes_freed += size
        except OSError:
            continue
    return {
        "ok": True,
        "removed": removed,
        "bytes_freed": bytes_freed,
        "max_age_days": max_age_days,
    }
