#!/usr/bin/env python3
"""Targeted pytest for the DRAFT fast path.

A draft PR -- and any push to a PR that is not the ready-for-review transition
-- runs this instead of the full suite; the full suite still runs once when the
PR becomes ready for review. The selection is a mechanism derived from the diff,
never a hardcoded name:

  * every changed test module under the test roots runs directly;
  * a changed conftest.py runs the test modules under its directory tree;
  * every changed source module under the source roots runs the test modules
    that import it AND name a function the change touches (same changed-function
    logic the mutation gate uses). A source file with no changed function
    (a constant, a class attribute, ...) falls back to its importers.

If the union is empty (a docs/workflow-only change) the run reports it and
exits 0 -- there is nothing to run, and that is not a defect. A non-empty
selection that collects zero tests fails closed (pytest's exit code): a
selection that runs nothing is a broken selection, not a green one.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutation_gate import changed_lines, functions_touching, module_tokens  # noqa: E402


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def repo_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=Path.cwd()).strip())


def changed_files(repo: Path, base: str) -> list[str]:
    # The caller fetches the base (same pattern as the mutation gate steps).
    out = git("diff", "--name-only", f"{base}...HEAD", cwd=repo)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _under_any(rel: Path, roots: Sequence[str]) -> bool:
    for root in roots:
        try:
            rel.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _import_pattern(module: str) -> re.Pattern[str]:
    parts = module.split(".")
    alts = [rf"import\s+{re.escape(module)}\b", rf"from\s+{re.escape(module)}\s+import"]
    if len(parts) > 1:
        parent = ".".join(parts[:-1])
        leaf = parts[-1]
        alts.append(rf"from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(leaf)}\b")
    return re.compile("|".join(alts))


def _changed_function_names(repo: Path, path: str, lines: set[int]) -> set[str]:
    try:
        tree = ast.parse((repo / path).read_text("utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    return {fn.name for fn in functions_touching(tree, lines)}


def _match(repo: Path, module: str, fns: set[str], test_roots: Sequence[str]) -> list[str]:
    """Test modules that import ``module``; if ``fns`` is non-empty, a module
    must also name one of them (the same tight match the mutation gate uses),
    so a change to one function in a widely-imported module stays targeted."""
    import_re = _import_pattern(module)
    name_re = re.compile("|".join(rf"\b{re.escape(fn)}\b" for fn in sorted(fns))) if fns else None
    hits: list[str] = []
    for root in test_roots:
        for t in sorted((repo / root).rglob("test_*.py")):
            try:
                text = t.read_text("utf-8", "replace")
            except OSError:
                continue
            if import_re.search(text) and (name_re is None or name_re.search(text)):
                hits.append(t.relative_to(repo).as_posix())
    return hits


def select_tests(
    repo: Path,
    src_roots: Sequence[str],
    test_roots: Sequence[str],
    changed: Sequence[str],
    changed_line_map: dict[str, set[int]],
) -> list[str]:
    out: list[str] = []
    for f in changed:
        rel = Path(f)
        if rel.name == "conftest.py":
            for t in sorted((repo / f).parent.rglob("test_*.py")):
                out.append(t.relative_to(repo).as_posix())
        elif rel.name.startswith("test_") or rel.name.endswith("_test.py"):
            out.append(f)
        elif rel.suffix == ".py" and _under_any(rel, src_roots) and not _under_any(rel, test_roots):
            for tok in module_tokens(f, list(src_roots), repo):
                fns = _changed_function_names(repo, f, changed_line_map.get(f, set()))
                out.extend(_match(repo, tok, fns, test_roots))
    return list(dict.fromkeys(out))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="base ref to diff BASE...HEAD against")
    ap.add_argument("--src", action="append", default=[], help="source root(s), e.g. backend/app")
    ap.add_argument("--tests", action="append", default=[], help="test root(s), e.g. backend/tests")
    ap.add_argument("--workdir", default="backend", help="directory pytest runs from")
    ap.add_argument("--pytest-arg", action="append", default=[], help="extra pytest args")
    a = ap.parse_args(argv)

    repo = repo_root()
    src_roots = a.src or ["backend/app"]
    test_roots = a.tests or ["backend/tests"]

    changed = changed_files(repo, a.base)
    line_map = changed_lines(a.base, repo, list(src_roots))
    selected = select_tests(repo, src_roots, test_roots, changed, line_map)
    if not selected:
        print("targeted pytest: no test files touched by this change (nothing to run)")
        return 0

    rel = [str(Path(t).relative_to(a.workdir)) for t in selected]
    print(f"targeted pytest: {len(rel)} touched file(s) -> {', '.join(rel)}")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-rfs", *a.pytest_arg, *rel],
        cwd=repo / a.workdir,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
