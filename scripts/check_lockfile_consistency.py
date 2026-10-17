#!/usr/bin/env python3
"""Fail-closed lockfile twin: a manifest change without its lock is a red check.

The #244 class: dependabot bumped ``backend/requirements.txt``
(prometheus-client 0.21.1 -> 0.26.0) and left ``backend/requirements.lock``
untouched. ``dependabot-automerge.yml`` then armed
``gh pr merge --auto --squash`` with zero lockfile inspection. The next such
PR auto-merges inert — CI and install keep the old pin.

This script is the twin. CI runs it against the PR (or commit) diff. A red
check is what blocks auto-merge. Automerge must not be the only gate and
must not skip CI.

A lock-only change is OK. A manifest + lock change is OK. A manifest change
without a lock delta is not.

Usage:
  python3 scripts/check_lockfile_consistency.py --changed PATH [PATH ...]
  python3 scripts/check_lockfile_consistency.py --stdin
  python3 scripts/check_lockfile_consistency.py --base REF [--head REF]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Iterable, Sequence

# Manifest -> one or more acceptable lock twins. Any one lock delta satisfies
# the pair. Frontend uses npm (package-lock.json); yarn.lock is accepted if
# that is the lock a change actually ships.
PAIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend/requirements.txt", ("backend/requirements.lock",)),
    (
        "frontend/package.json",
        ("frontend/package-lock.json", "frontend/yarn.lock"),
    ),
)


def normalize_path(path: str) -> str:
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def inconsistencies(changed: Iterable[str]) -> list[str]:
    """Return human-readable findings for a synthetic or real path set.

    Empty / unrelated / lock-only / matching twins -> no findings.
    """
    changed_set = {normalize_path(p) for p in changed if normalize_path(p)}
    findings: list[str] = []
    for manifest, locks in PAIRS:
        if manifest not in changed_set:
            continue
        if any(lock in changed_set for lock in locks):
            continue
        shown = " or ".join(locks)
        findings.append(
            f"lockfile-consistency: {manifest} changed without {shown}. "
            "A requirements/package bump with no lock delta is the #244 class "
            "and must not merge. Update the lock in the same commit/PR "
            "(lock-only and manifest+lock are OK)."
        )
    return findings


def changed_from_git(base: str, head: str = "HEAD", cwd: str | None = None) -> list[str]:
    """Triple-dot name-only diff: merge-base(base, head) -> head (PR shape)."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRT", f"{base}...{head}"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"git diff {base}...{head} failed: {err}")
    return [normalize_path(line) for line in proc.stdout.splitlines() if line.strip()]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--changed",
        nargs="+",
        metavar="PATH",
        help="Synthetic or explicit changed paths (mutation-test entry).",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read changed paths, one per line, from stdin.",
    )
    parser.add_argument("--base", help="Git ref/SHA for the PR (or commit) base.")
    parser.add_argument(
        "--head",
        default="HEAD",
        help="Git ref/SHA for the PR head (default HEAD).",
    )
    return parser.parse_args(argv)


def resolve_changed(args: argparse.Namespace) -> list[str]:
    modes = sum(bool(x) for x in (args.changed, args.stdin, args.base))
    if modes != 1:
        raise SystemExit(
            "lockfile-consistency: specify exactly one of --changed, --stdin, or --base"
        )
    if args.changed:
        return [normalize_path(p) for p in args.changed]
    if args.stdin:
        return [normalize_path(line) for line in sys.stdin]
    return changed_from_git(args.base, args.head)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if argv is not None and not (args.changed or args.stdin or args.base):
            # parse_args already ran; resolve_changed enforces the mode.
            pass
        paths = resolve_changed(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise
    except Exception as exc:
        print(f"lockfile-consistency: cannot determine changed files: {exc}", file=sys.stderr)
        return 1
    findings = inconsistencies(paths)
    if findings:
        for line in findings:
            print(line, file=sys.stderr)
        return 1
    print("ok: lockfile twins consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
