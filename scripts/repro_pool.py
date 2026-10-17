#!/usr/bin/env python3
"""The release cycle's rotation pool: load it, check it, name what it holds.

The pool is DATA (``backend/tests/repro_pool``): an index, ``pool.json``,
listing blueprint files in the order the rotation walks them, and one file per
blueprint carrying what the Floor needs to build it without a question left
open -- the brief, the vertical, the country and currency, the build level --
plus a deterministic id (the file's own name) and a display name.

Two readers share this module, so both see the same pool:

* ``scripts/release_cycle.py`` picks each cycle's blueprints from it;
* ``scripts/scan_hardwiring.py`` forbids every blueprint's id, name and
  vertical as a string literal in Factory code (form ``blueprint_name``), so
  nothing in the Factory can branch on which blueprint is running. The
  forbidden set is read from the pool files, so adding a blueprint extends the
  gate with no edit here.

A pool that cannot be read, or is not complete, is an error, never an empty
pool: a rotation over nothing builds nothing, and a gate over nothing passes
everything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
#: The index, repo-relative. Its declared order is the order fresh picks are
#: taken in; which a cycle builds is scripts/release_cycle.py select_rotation.
POOL_INDEX_REL = "backend/tests/repro_pool/pool.json"
POOL_INDEX = ROOT / POOL_INDEX_REL
INDEX_SCHEMA = "repro_pool.v1"
BLUEPRINT_SCHEMA = "repro_blueprint.v1"
#: The build levels the Floor defines (app.factory.build.build_level).
BUILD_LEVELS = ("prototype", "light", "pilot", "production")
#: What every blueprint must carry to go straight through the Floor:
#: draft -> set_build_level -> confirm_intake -> approve.
REQUIRED = ("id", "name", "vertical", "country", "currency", "build_level", "brief")
#: The fields whose VALUES the hardwiring gate forbids as literals in code.
IDENTITY_FIELDS = ("id", "name", "vertical")

_ID_SHAPE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
_COUNTRY_SHAPE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_SHAPE = re.compile(r"^[A-Z]{3}$")


class PoolError(RuntimeError):
    """The pool cannot be used as declared (missing, incomplete, inconsistent)."""


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PoolError(f"{path}: unreadable ({exc})") from exc


def _check_blueprint(doc: Any, file: str) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise PoolError(f"{file}: not a JSON object")
    if doc.get("schema") != BLUEPRINT_SCHEMA:
        raise PoolError(f"{file}: schema must be {BLUEPRINT_SCHEMA!r}")
    for field in REQUIRED:
        if not str(doc.get(field) or "").strip():
            raise PoolError(f"{file}: {field!r} is required")
    bid = str(doc["id"])
    if not _ID_SHAPE.match(bid):
        raise PoolError(f"{file}: id {bid!r} must be lower-case letters, digits and _")
    if file != bid + ".json":
        raise PoolError(f"{file}: id {bid!r} must be the file's own name ({bid}.json)")
    if not _COUNTRY_SHAPE.match(str(doc["country"])):
        raise PoolError(f"{file}: country must be a 2-letter upper-case code")
    if not _CURRENCY_SHAPE.match(str(doc["currency"])):
        raise PoolError(f"{file}: currency must be a 3-letter upper-case code")
    if doc["build_level"] not in BUILD_LEVELS:
        raise PoolError(f"{file}: build_level must be one of {', '.join(BUILD_LEVELS)}")
    return {**doc, "file": file}


def load_index(path: Path | str = POOL_INDEX) -> Dict[str, Any]:
    index_path = Path(path)
    index = _read(index_path)
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA:
        raise PoolError(f"{index_path}: schema must be {INDEX_SCHEMA!r}")
    files = index.get("blueprints")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        raise PoolError(f"{index_path}: 'blueprints' must be a non-empty list of file names")
    if len(files) != len(set(files)):
        raise PoolError(f"{index_path}: a blueprint is listed twice")
    picks = index.get("picks_per_cycle")
    if not isinstance(picks, int) or isinstance(picks, bool) or not 1 <= picks <= len(files):
        raise PoolError(f"{index_path}: picks_per_cycle must be 1..{len(files)}")
    return index


def load_pool(path: Path | str = POOL_INDEX) -> List[Dict[str, Any]]:
    """The pool's blueprints in the index's DECLARED order (never the
    filesystem's). Every blueprint file beside the index must be listed: an
    unlisted one would never be built."""
    index_path = Path(path)
    index = load_index(index_path)
    files: List[str] = index["blueprints"]
    folder = index_path.parent
    on_disk = sorted(p.name for p in folder.glob("*.json") if p.name != index_path.name)
    unlisted = sorted(set(on_disk) - set(files))
    if unlisted:
        raise PoolError(f"{index_path}: not in the rotation: {', '.join(unlisted)}")
    missing = [f for f in files if not (folder / f).is_file()]
    if missing:
        raise PoolError(f"{index_path}: listed but absent: {', '.join(missing)}")
    pool = [_check_blueprint(_read(folder / f), f) for f in files]
    for field in ("id", "vertical"):
        values = [bp[field] for bp in pool]
        if len(values) != len(set(values)):
            raise PoolError(f"{index_path}: two blueprints share one {field}")
    return pool


def identity_literals(pool: Iterable[Dict[str, Any]]) -> FrozenSet[str]:
    """Every blueprint's id, name and vertical, lower-cased: the strings Factory
    code may never spell (scan_hardwiring form ``blueprint_name``)."""
    out = set()
    for bp in pool:
        for field in IDENTITY_FIELDS:
            value = str(bp.get(field) or "").strip().lower()
            if value:
                out.add(value)
    return frozenset(out)
