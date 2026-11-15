#!/usr/bin/env python3
"""Coverage-derived test mapping for the mutation gate (CI-M-a, advisor 23:12Z).

The mutation gate's static ``tests_for`` over-matches: any test file that
imports a module is treated as covering EVERY function in it, so a god-file
like ``app.factory.build.runner`` maps 81 test files per function and CI-M-a
runs past its 60-minute budget.

This generator replaces that with coverage: it runs the suite once, records
which test FILE calls each source function, and writes the mapping. A test
that never calls a function cannot kill its mutant, so dropping the rest
weakens nothing.

Stdlib only (``sys.setprofile`` + an in-process pytest plugin; no coverage
package).
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

#: A raw execution triple: (test_file, source_path, function_name).
Execution = tuple[str, str, str]


def build_mapping(executions: Sequence[Execution]) -> dict[str, dict[str, list[str]]]:
    """Collapse execution triples into ``source_path -> function -> test files``.

    Deterministic (files and functions sorted). This is the pure core the
    two-input tests target; the tracing machinery below only feeds it.
    """
    out: dict[str, dict[str, set[str]]] = {}
    for test_file, source_path, fn_name in executions:
        out.setdefault(source_path, {}).setdefault(fn_name, set()).add(test_file)
    return {
        mod: {fn: sorted(files) for fn, files in sorted(funcs.items())}
        for mod, funcs in sorted(out.items())
    }


def load_mapping(repo: Path, mapping_path: str) -> dict[str, dict[str, list[str]]]:
    """Read a committed mapping file; absence or corruption degrades to {}."""
    path = repo / mapping_path
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _function_spans(source_path: Path) -> dict[str, set[int]]:
    """Map function name -> body line numbers (outer and nested both counted)."""
    try:
        tree = ast.parse(source_path.read_text("utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    spans: dict[str, set[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.setdefault(node.name, set()).update(
                range(node.lineno, (node.end_lineno or node.lineno) + 1)
            )
    return spans


class _FileContextPlugin:
    """Switch coverage's dynamic context to the current test FILE (not function),
    so one run attributes each covered source line to the file that ran it."""

    def __init__(self, cov, repo: Path):
        self.cov = cov
        self.repo = repo

    def pytest_configure(self, config):
        self.cov.start()

    def pytest_unconfigure(self, config):
        self.cov.stop()

    def pytest_runtest_protocol(self, item, nextitem):
        p = Path(item.path)
        try:
            ctx = p.relative_to(self.repo).as_posix()
        except ValueError:
            ctx = str(p)
        self.cov.switch_context(ctx)


def collect(repo: Path, src_roots: Sequence[str], test_files: Sequence[str], pytest_args: Sequence[str], workdir: str = ".") -> list[Execution]:
    """Run pytest once under coverage with per-FILE dynamic contexts.

    ``switch_context(file)`` costs ~60 switches (one per test file), not the
    ~4400 per-test switches of ``--cov-context=test``, so this is ~1.1x instead
    of ~30x. A source line only has a context when a test FILE executed it, so
    a function's body maps to the files that actually ran it (not mere import).
    """
    import os

    import coverage
    import pytest

    wd = repo / workdir
    rel_tests = (
        [str(Path(t).relative_to(workdir)) for t in test_files]
        if workdir != "."
        else list(test_files)
    )
    src_abs = [str((repo / r).resolve()) for r in src_roots]
    cov = coverage.Coverage(source=src_abs, data_file=None, branch=False)
    plugin = _FileContextPlugin(cov, repo)
    old = os.getcwd()
    os.chdir(wd)
    sys.path.insert(0, str(wd))
    try:
        pytest.main(
            ["-q", "-p", "no:cacheprovider", *pytest_args, *rel_tests],
            plugins=[plugin],
        )
    finally:
        os.chdir(old)
        try:
            sys.path.remove(str(wd))
        except ValueError:
            pass
    data = cov.get_data()
    executions: list[Execution] = []
    for filename in data.measured_files():
        try:
            rel_src = Path(filename).relative_to(repo).as_posix()
        except ValueError:
            continue
        spans = _function_spans(Path(filename))
        if not spans:
            continue
        ctx_by_line = data.contexts_by_lineno(filename) or {}
        for fn_name, lines in spans.items():
            files: set[str] = set()
            for ln in lines:
                for ctx in ctx_by_line.get(ln, ()):
                    if ctx:
                        files.add(ctx)
            for f in sorted(files):
                executions.append((f, rel_src, fn_name))
    return executions


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", action="append", required=True, help="source root(s) to trace")
    ap.add_argument("--tests", action="append", required=True, help="test root(s)")
    ap.add_argument("--out", required=True, help="mapping JSON path (repo-relative)")
    ap.add_argument("--workdir", default=".", help="directory pytest runs from (e.g. backend)")
    ap.add_argument("--pytest-arg", action="append", default=[])
    a = ap.parse_args(argv)

    repo = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())
    test_files: list[str] = []
    for root in a.tests:
        test_files.extend(str(p.relative_to(repo)) for p in sorted((repo / root).rglob("test_*.py")))

    executions = collect(repo, a.src, test_files, a.pytest_arg, workdir=a.workdir)
    mapping = build_mapping(executions)
    out_path = repo / a.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", "utf-8")
    n_fns = sum(len(funcs) for funcs in mapping.values())
    print(f"mapping: {len(executions)} executions -> {len(mapping)} files, {n_fns} functions -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
