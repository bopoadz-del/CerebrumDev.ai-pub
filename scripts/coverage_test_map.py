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


class _CoveragePlugin:
    """A pytest plugin that records which test file calls each source function.

    ``sys.setprofile`` fires on every Python call; we keep only calls whose
    filename lives under a source root (fast string-prefix check, so the
    profile overhead stays off the hot test path) and attribute them to the
    test file currently running.
    """

    def __init__(self, repo: Path, src_roots: Sequence[str]):
        self.repo = repo
        self.src_prefixes = [str((repo / r).resolve()) for r in src_roots]
        self.current_test: str | None = None
        self.executions: list[Execution] = []
        self._prev_profile = None

    def _profile(self, frame, event, arg):
        if event != "call" or self.current_test is None:
            return
        filename = frame.f_code.co_filename
        for prefix in self.src_prefixes:
            if filename.startswith(prefix):
                rel = Path(filename).relative_to(self.repo).as_posix()
                self.executions.append((self.current_test, rel, frame.f_code.co_name))
                break

    def pytest_configure(self, config):
        self._prev_profile = sys.getprofile()
        sys.setprofile(self._profile)

    def pytest_unconfigure(self, config):
        sys.setprofile(self._prev_profile)

    def pytest_runtest_protocol(self, item, nextitem):
        p = Path(item.path)
        try:
            self.current_test = p.relative_to(self.repo).as_posix()
        except ValueError:
            self.current_test = str(p)


def collect(repo: Path, src_roots: Sequence[str], test_files: Sequence[str], pytest_args: Sequence[str], workdir: str = ".") -> list[Execution]:
    """Run pytest once in-process with the tracing plugin; return executions.

    pytest runs from ``repo/workdir`` (so it finds backend/pytest.ini and the
    conftests); the plugin still records repo-relative test paths.
    """
    import os

    import pytest

    plugin = _CoveragePlugin(repo, src_roots)
    rel_tests = (
        [str(Path(t).relative_to(workdir)) for t in test_files]
        if workdir != "."
        else list(test_files)
    )
    old = os.getcwd()
    os.chdir(repo / workdir)
    # The workdir must be importable (conftest.py does ``from tests.factory``,
    # test modules do ``from app.factory``); chdir alone does not add it.
    sys.path.insert(0, str(repo / workdir))
    try:
        pytest.main(
            ["-q", "-p", "no:cacheprovider", *pytest_args, *rel_tests],
            plugins=[plugin],
        )
    finally:
        os.chdir(old)
        try:
            sys.path.remove(str(repo / workdir))
        except ValueError:
            pass
    return plugin.executions


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
