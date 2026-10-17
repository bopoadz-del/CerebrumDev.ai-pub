#!/usr/bin/env python3
"""Fail-closed dependency audit: pip-audit + npm audit, no bare ignore.

A remaining advisory is allowed only when a dated registry note still
matches the live finding (empty fix_versions, or the installed pin
already satisfies the published fix). A scanner summary is not enough —
the note has to name the declared constraint.

Usage:
  python3 scripts/dep_audit.py
  python3 scripts/dep_audit.py --python
  python3 scripts/dep_audit.py --npm
  python3 scripts/dep_audit.py --pip-json PATH --npm-json PATH
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = (
    REPO_ROOT / "docs" / "security" / "dependency-adjudications" / "registry.json"
)
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Forbidden in workflows: a suppression that is not the registry.
BARE_IGNORE_MARKERS = (
    "--ignore-vuln",
    "npm audit --ignore",
    "npm audit --audit-level=none",
)


class Finding:
    __slots__ = ("ecosystem", "package", "version", "vuln_id", "aliases", "fix_versions")

    def __init__(
        self,
        *,
        ecosystem: str,
        package: str,
        version: str,
        vuln_id: str,
        aliases: Iterable[str] = (),
        fix_versions: Iterable[str] = (),
    ) -> None:
        self.ecosystem = ecosystem
        self.package = package
        self.version = version
        self.vuln_id = vuln_id
        self.aliases = tuple(sorted({a for a in aliases if a}))
        self.fix_versions = tuple(v for v in fix_versions if v)

    @property
    def keys(self) -> set[str]:
        return {self.vuln_id, *self.aliases}


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"dep-audit: missing registry {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def registry_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map every id and alias to its row. Duplicate keys must agree."""
    index: dict[str, dict[str, Any]] = {}
    for row in registry.get("adjudications") or []:
        keys = [row["id"], *(row.get("aliases") or [])]
        for key in keys:
            existing = index.get(key)
            if existing is not None and existing["id"] != row["id"]:
                raise ValueError(f"dep-audit: {key} maps to both {existing['id']} and {row['id']}")
            index[key] = row
    return index


def findings_from_pip_audit(data: dict[str, Any] | list) -> list[Finding]:
    deps = data if isinstance(data, list) else data.get("dependencies") or []
    seen: set[tuple[str, str, str]] = set()
    out: list[Finding] = []
    for dep in deps:
        name = str(dep.get("name") or dep.get("package") or "")
        version = str(dep.get("version") or dep.get("installed_version") or "")
        for vuln in dep.get("vulns") or dep.get("vulnerabilities") or []:
            vid = str(vuln.get("id") or "")
            if not name or not vid:
                continue
            key = (name.lower(), version, vid)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Finding(
                    ecosystem="pip",
                    package=name,
                    version=version,
                    vuln_id=vid,
                    aliases=vuln.get("aliases") or [],
                    fix_versions=vuln.get("fix_versions") or vuln.get("fixed_versions") or [],
                )
            )
    return out


def findings_from_npm_audit(data: dict[str, Any]) -> list[Finding]:
    vulns = data.get("vulnerabilities") or {}
    out: list[Finding] = []
    seen: set[str] = set()
    for name, info in vulns.items():
        via = info.get("via") or []
        if not isinstance(via, list):
            continue
        for item in via:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            ghsa = url.rsplit("/", 1)[-1] if "github.com/advisories/" in url else ""
            vid = ghsa or str(item.get("source") or "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            out.append(
                Finding(
                    ecosystem="npm",
                    package=str(item.get("name") or name),
                    version=str(item.get("range") or info.get("range") or ""),
                    vuln_id=vid,
                    aliases=[ghsa] if ghsa else [],
                    # npm "fixAvailable" is not a version pin; treat True as
                    # "a fix exists" so an un-upgraded finding cannot hide.
                    fix_versions=["available"] if info.get("fixAvailable") else [],
                )
            )
    return out


def _note_ok(row: dict[str, Any], repo: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    note = str(row.get("note") or "")
    dated = str(row.get("date") or "")
    vid = str(row.get("id") or "")
    if not dated:
        errors.append(f"{vid}: registry row has no date")
    else:
        try:
            date.fromisoformat(dated)
        except ValueError:
            errors.append(f"{vid}: date {dated!r} is not ISO YYYY-MM-DD")
    if not note:
        errors.append(f"{vid}: registry row has no note path")
        return errors
    path = repo / note
    if not path.is_file():
        errors.append(f"{vid}: note missing: {note}")
        return errors
    text = path.read_text(encoding="utf-8")
    if vid not in text:
        errors.append(f"{vid}: note {note} does not name the advisory id")
    if dated and dated not in text:
        errors.append(f"{vid}: note {note} does not name the adjudication date")
    if "Decision:" not in text and "decision" not in text.lower():
        errors.append(f"{vid}: note {note} has no decision")
    return errors


def evaluate(
    findings: Iterable[Finding],
    registry: dict[str, Any],
    *,
    repo: Path = REPO_ROOT,
) -> list[str]:
    """Return human-readable failures. Empty means the resolve is allowed."""
    errors: list[str] = []
    index = registry_index(registry)
    for row in registry.get("adjudications") or []:
        errors.extend(_note_ok(row, repo))
        if str(row.get("decision") or "") == "suppress":
            errors.append(f"{row.get('id')}: decision 'suppress' is a bare suppression")
    for finding in findings:
        row = next((index[k] for k in finding.keys if k in index), None)
        if row is None:
            errors.append(
                f"undocumented {finding.ecosystem} advisory {finding.vuln_id} "
                f"in {finding.package}=={finding.version}. Write a dated note "
                f"+ registry row, or upgrade. Never --ignore-vuln."
            )
            continue
        if finding.fix_versions and str(row.get("decision") or "") == "accept_until_fix":
            errors.append(
                f"stale adjudication {row['id']} ({finding.vuln_id}): "
                f"pip-audit now lists fix_versions={list(finding.fix_versions)}. "
                f"Upgrade {finding.package} or rewrite the {row.get('date')} note. "
                f"Do not keep a silent ignore."
            )
    return errors


def pip_audit_argv(requirements: Path) -> list[str]:
    """Audit the factory pin file, not the runner's site-packages.

    A local-env scan on GitHub Actions reports PYSEC-2026-3447 in the
    toolchain's ``setuptools`` (79.0.1 on the 3.11 image). That package is
    not in ``requirements.txt``. ``-r`` is the Linux resolve this job
    adjudicates. Do not replace it with a bare ``--ignore-vuln``.
    """
    return [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        str(requirements),
        "--format",
        "json",
        "--progress-spinner",
        "off",
    ]


def run_pip_audit(cwd: Path | None = None) -> dict[str, Any]:
    backend = cwd or REPO_ROOT / "backend"
    requirements = backend / "requirements.txt"
    proc = subprocess.run(
        pip_audit_argv(requirements),
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
    )
    # pip-audit exits 1 when it finds vulns; that is the input, not the gate.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"pip-audit failed (exit {proc.returncode}): {proc.stderr or proc.stdout}"
        )
    raw = proc.stdout.strip() or "{}"
    return json.loads(raw)


def run_npm_audit(cwd: Path | None = None) -> dict[str, Any]:
    frontend = cwd or REPO_ROOT / "frontend"
    proc = subprocess.run(
        ["npm", "audit", "--json", "--package-lock-only"],
        cwd=frontend,
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (proc.stdout or "").strip() or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"npm audit did not return JSON (exit {proc.returncode}): "
            f"{proc.stderr or proc.stdout[:400]}"
        ) from exc


def workflow_has_bare_ignore(text: str) -> list[str]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for marker in BARE_IGNORE_MARKERS:
            if marker in code:
                hits.append(f"{CI_YML.name}:{i}: {marker}")
    return hits


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--python", action="store_true", help="Run pip-audit only.")
    parser.add_argument("--npm", action="store_true", help="Run npm audit only.")
    parser.add_argument("--pip-json", type=Path, help="Synthetic pip-audit JSON.")
    parser.add_argument("--npm-json", type=Path, help="Synthetic npm audit JSON.")
    parser.add_argument(
        "--registry",
        type=Path,
        default=REGISTRY_PATH,
        help="Adjudication registry (default: in-repo).",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="Repo root (notes are resolved from here).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo
    try:
        registry = load_registry(args.registry)
    except Exception as exc:
        print(f"dep-audit: {exc}", file=sys.stderr)
        return 2

    run_python = args.python or args.pip_json is not None
    run_npm = args.npm or args.npm_json is not None
    if not (run_python or run_npm):
        run_python = run_npm = True

    findings: list[Finding] = []
    try:
        if run_python:
            if args.pip_json:
                data = json.loads(args.pip_json.read_text(encoding="utf-8"))
            else:
                data = run_pip_audit()
            findings.extend(findings_from_pip_audit(data))
        if run_npm:
            if args.npm_json:
                data = json.loads(args.npm_json.read_text(encoding="utf-8"))
            else:
                data = run_npm_audit()
            findings.extend(findings_from_npm_audit(data))
    except Exception as exc:
        print(f"dep-audit: scanner failed: {exc}", file=sys.stderr)
        return 1

    errors = evaluate(findings, registry, repo=repo)
    ci_text = (repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    errors.extend(f"bare suppression {h}" for h in workflow_has_bare_ignore(ci_text))

    if errors:
        for line in errors:
            print(f"dep-audit: {line}", file=sys.stderr)
        return 1
    pip_n = sum(1 for f in findings if f.ecosystem == "pip")
    npm_n = sum(1 for f in findings if f.ecosystem == "npm")
    adj = len(registry.get("adjudications") or [])
    print(
        f"ok: dep-audit python={pip_n} npm={npm_n} "
        f"adjudications={adj} (dated notes + twins, no bare ignore)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
