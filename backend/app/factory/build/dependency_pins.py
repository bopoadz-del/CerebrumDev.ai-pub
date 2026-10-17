"""The framework versions every Factory-built product installs. One source.

Live 2026-10-07 (e9efdf77): a product built the same way as the day before
failed its Store gate before the first check, because the base requirements
were open ranges (``starlette>=0.37``) and an upstream release moved a
dependency between two builds. A Factory whose output depends on the day it
ran is not reproducible.

So the Factory pins the stack it renders -- the web framework, its server,
its ASGI toolkit and validation layer, the ORM and migrations, the Postgres
driver, and the test stack -- to versions the Factory's own suite runs
(backend/requirements.txt; a test keeps the two equal). The pin set is ONE
file, ``factory_pins.lock.json``: the top-level versions plus every
distribution they resolve to on the product image's platform, each with what
its wheel declares, so a test proves the set is closed -- every dependency of
a pinned distribution is itself pinned at a version that satisfies it, and
two image builds of one commit resolve the same distributions. From it:

* the base lines of requirements.txt render as ``==`` pins
  (roles_handlers._render_requirements, merged into its Factory block);
* requirements-dev.txt renders the test stack the same way;
* ``constraints.txt`` (Factory-owned, re-rendered on every re-entry) carries
  the whole closed set, and the Dockerfile contract installs with
  ``-c constraints.txt`` (floor line docker_health_200).

A declaration that bounds a pinned distribution differently wins, so a pin
never makes an install unresolvable: any line of requirements.txt or
requirements-dev.txt -- the product's own, or a vendored block's -- whose
specifier the pin does not satisfy drops that constraint, and the line stays
exactly as declared. Bounds that live in a dependency's own metadata are
pip's to honour: the table pins only the framework stack, so a distribution a
vendored block brings in (an OCR block, for one, holds its imaging library
below a major version) is never named here and never fought.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

#: The Factory-owned constraints file at the product root.
CONSTRAINTS_REL = "constraints.txt"

#: The one pin set (regenerate with scripts/regenerate_factory_pins.py).
LOCK_PATH = Path(__file__).with_name("factory_pins.lock.json")


def load_lock() -> Dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


#: distribution -> exact version: the top-level stack first, then the rest of
#: its closure, in the lock's order.
FACTORY_PINS: Dict[str, str] = {
    dist: str(row["version"]) for dist, row in load_lock()["distributions"].items()
}

#: The pinned distributions that only tests need; never in requirements.txt.
TEST_ONLY = ("pytest", "httpx")


def dist_name(line: str) -> str:
    """``psycopg[binary]==3.3.6  # why`` -> ``psycopg``; "" for a comment,
    blank, or pip option line."""
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-"):
        return ""
    return re.split(r"[<>=!~\[; ]", text, 1)[0].strip().lower().replace("_", "-")


def pin(dist: str, *, extras: str = "") -> str:
    """The requirement line for a pinned distribution."""
    key = dist.lower()
    return "%s%s==%s" % (key, ("[%s]" % extras) if extras else "", FACTORY_PINS[key])


def _specifier(line: str) -> str:
    text = line.split("#", 1)[0].split(";", 1)[0].strip()
    text = re.sub(r"\[[^\]]*\]", "", text)
    return text[len(re.split(r"[<>=!~ ]", text, 1)[0]):].strip()


def excludes_pin(line: str) -> bool:
    """Does this product line declare its distribution with a specifier the
    Factory pin does not satisfy? An unparseable specifier counts as
    excluding: the product's declaration is then left alone, never fought."""
    dist = dist_name(line)
    if dist not in FACTORY_PINS:
        return False
    spec = _specifier(line)
    if not spec:
        return False
    try:
        from packaging.specifiers import SpecifierSet

        return FACTORY_PINS[dist] not in SpecifierSet(spec)
    except Exception:
        return True


def render_constraints(declared: Iterable[str] = ()) -> str:
    """``constraints.txt``: every pin, minus those a declared line excludes."""
    overridden = {dist_name(line) for line in declared if excludes_pin(line)}
    lines: List[str] = [
        "# Rendered by the Factory from one table (dependency_pins.FACTORY_PINS);",
        "# re-rendered on every re-entry. Install with:",
        "#   pip install -c %s -r requirements.txt" % CONSTRAINTS_REL,
        "# Only the framework stack is pinned. A requirements line that bounds",
        "# a pinned distribution differently keeps its declaration, and the pin",
        "# is dropped; what a dependency's own metadata bounds is pip's to honour.",
    ]
    for dist in FACTORY_PINS:
        if dist not in overridden:
            lines.append(pin(dist))
    return "\n".join(lines) + "\n"


#: The requirement files whose declarations a pin must not fight.
DECLARING_FILES = ("requirements.txt", "requirements-dev.txt")


def constraints_for_tree(root: Path) -> str:
    """``render_constraints`` for a product tree: every line it declares."""
    declared: List[str] = []
    for rel in DECLARING_FILES:
        path = Path(root) / rel
        if path.is_file():
            declared.extend(path.read_bytes().decode("utf-8").splitlines())
    return render_constraints(declared)
