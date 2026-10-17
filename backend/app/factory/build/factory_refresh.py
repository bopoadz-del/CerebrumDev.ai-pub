"""Re-write the Factory's own files on a re-entered build.

A build resumed from an earlier run -- or attached from its cerebrum-builds
branch -- carries the Factory files of the Factory that FIRST built it. When
the Factory has since been fixed, those old copies keep failing the build:
live, a hotel platform built before the release-gate fix kept a
``scripts/release_gate.py`` that demands a withheld file, so every resume
failed its Docker image at 0/13 no matter how green the product was.

So on re-entry, before TESTER, the files the Factory owns outright are
re-rendered from the CURRENT templates. Only files that are pure Factory
templates with no coder content; the coder's files (handlers, models, routes,
dispatch) are never touched.

requirements.txt is MERGED, never replaced: every line the build declares
(a Postgres driver, say) stays, and only the vendored blocks' missing
obligations are added.

Before #683 the Factory appended its lines UNMARKED, under
``LEGACY_BLOCK_HEADER``, once per refresh. Those lines are the Factory's, not
the product's, but the marker reader cannot tell -- so a branch of record kept
every package an older Factory ever stamped, for blocks it no longer vendors
(live 2026-10-07: ``marker-pdf`` pinned ``pillow<11`` into a vineyard image
that imports neither, and 33 pillow advisories were billed to the writer).
The merge removes those legacy blocks and re-derives the Factory's lines into
its marked block, so a stale Factory line goes with the stamp that owns it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List

_LF = "\n"


def _dist(line: str):
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    return re.split(r"[<>=!~\[; ]", text, 1)[0].strip().lower().replace("_", "-")


def _write_if_changed(
    root: Path, rel: str, text: str, changed: List[str], *, shared: bool = False
) -> None:
    """Write ``text`` when it differs. A Factory-owned file is normalised to
    LF; a SHARED file (``shared=True``) is written exactly as given, so the
    product's own bytes -- line endings included -- are never rewritten."""
    path = root / rel
    if not shared:
        text = text.replace("\r\n", _LF)
    old = path.read_bytes().decode("utf-8") if path.is_file() else None
    if old is not None and not shared:
        old = old.replace("\r\n", _LF)
    if old == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    changed.append(rel)


#: The header the pre-#683 merge emitted above each unmarked block of
#: Factory lines (git show 588ab537^:backend/app/factory/build/factory_refresh.py).
#: The Factory's own former output, read only to remove it.
LEGACY_BLOCK_HEADER = (
    "# Packages this tree needs and did not declare: vendored block\n"
    "# imports, and framework features FastAPI does not declare\n"
    "# (refreshed by the factory)."
)


def strip_legacy_blocks(text: str) -> str:
    """``text`` without the unmarked blocks the pre-#683 Factory appended.

    That emitter wrote a blank line, ``LEGACY_BLOCK_HEADER``, then one
    requirement line per package -- and nothing else -- so a block ends at the
    first blank or comment line. Every other byte is returned as it was.
    """
    header = LEGACY_BLOCK_HEADER.split(_LF)
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    i = 0
    while i < len(lines):
        window = [line.rstrip("\r\n") for line in lines[i:i + len(header)]]
        if window != header:
            out.append(lines[i])
            i += 1
            continue
        if out and not out[-1].strip():
            out.pop()  # the blank line the emitter put before its header
        i += len(header)
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith("#"):
            i += 1
    return "".join(out)


def merged_requirements(root: Path) -> str:
    """The build's requirements.txt plus every package the tree needs and lacks.

    Two sources of missing packages: the vendored blocks' own imports, and the
    framework features the coder used whose backing distribution FastAPI does
    not declare (form parsing, templates, session cookies). Both are read off
    the tree, so a branch built before the Factory learned about either gets
    them on the resume that re-enters it.
    """
    from app.factory.build.block_obligations import dependency_obligations_on_disk
    from app.factory.build.factory_block import apply_block, outside
    from app.factory.build.roles_handlers import _render_requirements

    # requirements.txt is SHARED: the product's own lines stay byte-for-byte
    # (factory_block); the Factory's additions live only in its marked block,
    # recomputed from scratch each pass so the edit is idempotent.
    path = root / "requirements.txt"
    existing = strip_legacy_blocks(path.read_bytes().decode("utf-8") if path.is_file() else "")
    product = outside(existing).replace("\r\n", _LF)
    have = {d for d in (_dist(line) for line in product.split(_LF)) if d}
    extra = []
    for line in _render_requirements(dependency_obligations_on_disk(root), root=root).split(_LF):
        dist = _dist(line)
        if dist and dist not in have:
            extra.append(line)
            have.add(dist)
    if not extra and outside(existing) == existing:
        return existing
    body = (
        "# Packages this tree needs and did not declare: vendored block\n"
        "# imports, and framework features FastAPI does not declare.\n"
        + _LF.join(extra) + (_LF if extra else "")
    )
    return apply_block(existing, body)


def product_display_name(blueprint: Any) -> str:
    """The name a refresh renders under: the brief's product name, else its id."""
    return str(
        getattr(blueprint, "product_name", "")
        or getattr(blueprint, "product_id", "")
        or "Platform"
    )


def refresh_factory_files(
    root: Path, product_name: str, blueprint: Any = None, *, render_absent: bool = False
) -> List[str]:
    """Re-render Factory-owned files in ``root``; return the ones that changed.

    ``render_absent``: a BUILD in progress gets every Factory-owned file whether
    or not a copy is present (live cycle 3, 5410237b: the writer-owned-files
    guard removed a ci.yml the writer had created, this refresh rendered ci.yml
    only "if present", and every build shipped the base branch's pytest-only
    workflow -- Store gate 20/22). Off for a tree replayed or re-entered as it
    is: a file such a build never had is not created there.

    ``blueprint`` keeps a re-entered build's acceptance harness following the
    brief it declared — re-rendering without it would raise every signal and
    silently re-strict a build whose brief asked for less."""
    from app.factory.build.roles_handlers import _render_release_gate
    from app.factory.build.store_acceptance import render_acceptance_script, render_github_ci

    root = Path(root)
    changed: List[str] = []

    def owned(rel: str) -> bool:
        return render_absent or (root / rel).is_file()

    if owned("scripts/release_gate.py"):
        _write_if_changed(root, "scripts/release_gate.py", _render_release_gate(product_name), changed)
    if owned("scripts/acceptance.py"):
        _write_if_changed(
            root,
            "scripts/acceptance.py",
            render_acceptance_script(blueprint),
            changed,
        )
    from app.factory.build.writer_behaviour import SELF_CHECK_REL, render_self_check

    if owned(str(SELF_CHECK_REL)):
        _write_if_changed(root, SELF_CHECK_REL, render_self_check(), changed)
    if owned(".github/workflows/ci.yml"):
        _write_if_changed(root, ".github/workflows/ci.yml", render_github_ci(), changed)
    if (root / "requirements.txt").is_file():
        _write_if_changed(root, "requirements.txt", merged_requirements(root), changed, shared=True)
        from app.factory.build.dependency_pins import CONSTRAINTS_REL, constraints_for_tree

        # requirements.txt's companion, Factory-owned outright and read against
        # the lines just merged: a build first made before the pins existed
        # gets them on re-entry; a tree with no requirements gets nothing.
        _write_if_changed(root, CONSTRAINTS_REL, constraints_for_tree(root), changed)
    return changed
