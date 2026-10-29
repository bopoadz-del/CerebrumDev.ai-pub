#!/usr/bin/env python3
"""CI-M-b -- two-input test lint (ORDERS_29OCT_1710Z-B).

Every NEW test function in ``base...HEAD`` that asserts a computed value
(``assert ... == ...`` or ``!=``) must be ``pytest.mark.parametrize``d with
>= 2 distinct literal cases (a computed case list counts as 2). A test file
that does not parse makes the lint raise (red), never skip. Exits 1 on any
finding. Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _param_cases(fn: ast.FunctionDef) -> int:
    best = 0
    for d in fn.decorator_list:
        if not (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "parametrize"):
            continue
        kw = {k.arg: k.value for k in d.keywords}
        vals = d.args[1] if len(d.args) >= 2 else kw.get("argvalues")
        if vals is not None:
            if isinstance(vals, (ast.List, ast.Tuple)):
                distinct = {ast.dump(e) for e in vals.elts}
                best = max(best, len(distinct))
            else:
                best = max(best, 2)  # a computed case list: counted by pytest, trusted as >1 only if non-literal
    return best


def _asserts_value(fn: ast.FunctionDef) -> bool:
    for n in ast.walk(fn):
        # Any ==/!= anywhere in the asserted expression counts (chained
        # compares, ``a and b == c``, ``not x == y``), not just the first op.
        if isinstance(n, ast.Assert) and any(
            isinstance(c, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in c.ops)
            for c in ast.walk(n.test)
        ):
            return True
    return False


def _test_fns(src: str) -> dict[str, ast.FunctionDef]:
    # A test file that does not parse is not linted around: the SyntaxError
    # propagates and the gate goes red with it.
    tree = ast.parse(src)
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            out[n.name] = n
    return out


def lint_two_inputs(repo: Path, base: str, test_roots: Sequence[str]) -> list[str]:
    bad = []
    names = git("diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD", "--", *test_roots, cwd=repo).split()
    for path in names:
        if not re.search(r"(^|/)test_[^/]*\.py$", path):
            continue
        try:
            old = git("show", f"{base}:{path}", cwd=repo)
        except subprocess.CalledProcessError:
            old = ""
        before = set(_test_fns(old))
        for name, fn in _test_fns((repo / path).read_text("utf-8")).items():
            if name in before or not _asserts_value(fn):
                continue
            if _param_cases(fn) < 2:
                bad.append(f"{path}::{name}")
    return bad


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--tests", action="append", required=True, help="test root(s)")
    ap.add_argument("--summary", default="")
    a = ap.parse_args(argv)
    repo = Path(git("rev-parse", "--show-toplevel", cwd=Path.cwd()).strip())
    bad = lint_two_inputs(repo, a.base, a.tests)
    lines = ["## Two-input test lint", "", f"New single-input value tests: {len(bad)}"]
    lines += [f"- FAIL: `{t}` (needs parametrize with >= 2 distinct inputs)" for t in bad]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.summary:
        Path(a.summary).write_text(text, "utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
