"""Repair a factory-owned substrate module whose on-disk copy cannot satisfy
the NAME-level import contract of the factory's own stamped test.

D2 (live 2026-09-29, contractor platform — 10 projects, 6 users each): with
``stub_rate=1.0`` the WRITER left a stub ``app/domain_ops.py``. The substrate
backfills were "gaps only" — file exists → skip — so the stub survived, and
the factory's own stamped ``tests/test_domain_acceptance.py``
(``from app.domain_ops import OUTCOMES, perform_all``) died at COLLECTION.
The run then billed the agent a rework round for a module the factory, not the
agent, owns, and G5 halted on ``SAME_FAILURE_TWICE``.

"File exists" is not "contract satisfied". A factory-owned substrate module a
stamped test imports specific NAMES from must actually provide those names.
This module decides, per file:

  * write    — the file is absent; the backfill writes the canonical copy.
  * keep     — the file exists and provides every required name (agent wrote a
               real one, or a prior backfill already ran). Left untouched.
  * repair   — the file exists, is imported for specific names, and provides
               NONE of them: a stub the factory owns. Replaced with canonical.
  * conflict — the file provides SOME required names but not all: possibly real
               authored work the factory must not overwrite. Left untouched and
               reported; the caller halts the run as FACTORY (no rework).

A module imported only whole (``from app import backup``) has no name contract,
so it is never repaired — an agent-authored copy of such a module is preserved
exactly as before.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple


def provided_top_level_names(source: str) -> Set[str]:
    """Every name a module binds at module scope (defs, classes, assignments,
    imports). What ``from app.mod import X`` can actually resolve."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def required_names_by_module(test_sources: Iterable[str]) -> Dict[str, Set[str]]:
    """{module: {names}} the stamped suite imports as ``from app.<module>
    import a, b``. Module-level imports (``from app import backup``) carry no
    name contract and never appear here."""
    wanted: Dict[str, Set[str]] = {}
    for src in test_sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            mod = node.module or ""
            parts = mod.split(".")
            if len(parts) != 2 or parts[0] != "app":
                continue
            short = parts[1]
            for alias in node.names:
                if alias.name != "*":
                    wanted.setdefault(short, set()).add(alias.name)
    return wanted


def reconcile_substrate(
    workspace: Any,
    substrate: Iterable[Tuple[str, str]],
    test_sources: Iterable[str],
) -> Dict[str, List[str]]:
    """Gap-fill AND stub-repair. Returns written / skipped / repaired /
    conflicts. The caller halts FACTORY when ``conflicts`` is non-empty."""
    required = required_names_by_module(list(test_sources))
    written: List[str] = []
    skipped: List[str] = []
    repaired: List[str] = []
    conflicts: List[str] = []

    for rel, content in substrate:
        # A module never shadows a package of the same name the agent wrote.
        if rel.endswith(".py") and workspace.exists(rel[:-3]):
            skipped.append(f"{rel} (a package of the same name exists)")
            continue
        if not workspace.exists(rel):
            workspace.write_text(Path(rel), content)
            written.append(rel)
            continue

        # The file exists. Does a stamped test import specific names from it?
        module = rel[len("app/"):-len(".py")] if (
            rel.startswith("app/") and rel.endswith(".py")
        ) else None
        need = required.get(module or "", set())
        if not need:
            # No name contract (module-level import, or a doc/script): the
            # existing "gaps only" rule stands — the agent's bytes survive.
            skipped.append(rel)
            continue

        provided = provided_top_level_names(workspace.read_text(rel))
        missing = need - provided
        if not missing:
            skipped.append(rel)  # already satisfies the contract
        elif missing == need:
            # Provides none of the required names: a stub the factory owns.
            workspace.write_text(Path(rel), content)
            repaired.append(f"{rel} (missing {', '.join(sorted(missing))})")
        else:
            # Provides some but not all: possibly real authored work. Unsafe to
            # merge — leave it and let the caller halt FACTORY.
            conflicts.append(
                f"{rel} provides {', '.join(sorted(need & provided))} but not "
                f"{', '.join(sorted(missing))}"
            )

    return {
        "written": written,
        "skipped": skipped,
        "repaired": repaired,
        "conflicts": conflicts,
    }
