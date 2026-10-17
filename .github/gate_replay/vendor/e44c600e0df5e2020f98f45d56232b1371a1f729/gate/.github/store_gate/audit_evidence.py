#!/usr/bin/env python3
"""The Store gate's security-scan verdict and its evidence -- one rule.

The gate runs bandit over the product's ``app/`` and pip-audit over its
``requirements.txt``; the floor's audit checks read the verdict
(``STORE_AUDIT_CLEAN``). A red audit used to say only "unclean", so the writer
was handed a failure with nothing to change. This script owns the one rule
both outputs use:

* ``verdict <bandit.json> [<pip_audit.json>]`` prints ``1`` when no result is
  a blocking one, else ``0``. A missing or unreadable bandit report is ``0``
  (the scan did not prove the tree clean); a pip-audit report that was named
  but is unreadable is ``0`` too. Every pip-audit vulnerability blocks.
* ``measured <bandit.json> [<pip_audit.json>]`` prints ``1`` when every named
  report exists and parses -- the scan RAN -- else ``0``. An unmeasured scan
  exports no verdict at all: a ``0`` would read as the product's unclean
  code when it is the gate that never looked.
* ``evidence <harness.py> <bandit.json> [out_dir] [<pip_audit.json>]`` writes,
  for every check the product's harness declares in ``AUDIT_CHECKS`` (the
  floor's ``stage: audit``):

  - ``audit_evidence.json``: the blocking findings as display text, one per
    line (``<test_id> <file>:<line> [<severity>] <text>`` /
    ``<vuln_id> <package>==<version>``);
  - ``audit_evidence_rows.json``: the same findings as TYPED rows --
    ``{"kind": "file", "file", "line", "test_id", "severity", "text"}`` and
    ``{"kind": "dependency", "package", "version", "vuln_id"}`` -- so the
    Factory decides who owns each finding from its provenance record, never
    by parsing the text.

  The harness is read with ``ast``, never imported or run; the gate names no
  check itself.

Blocking = bandit severity HIGH, or bandit test B608 (SQL built from strings),
or any pip-audit vulnerability.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

BLOCKING_SEVERITY = "HIGH"
BLOCKING_TESTS = frozenset({"B608"})
EVIDENCE_ROWS = 40
OUT_NAME = "audit_evidence.json"
ROWS_OUT_NAME = "audit_evidence_rows.json"
KIND_FILE = "file"
KIND_DEPENDENCY = "dependency"


def blocking(result: dict) -> bool:
    return (
        str(result.get("issue_severity", "")).upper() == BLOCKING_SEVERITY
        or str(result.get("test_id", "")) in BLOCKING_TESTS
    )


def load_results(report: Path):
    """The bandit results, or None when the report is missing/unreadable."""
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    results = data.get("results") if isinstance(data, dict) else None
    return [r for r in results or [] if isinstance(r, dict)]


def load_dependencies(report):
    """pip-audit's dependencies, or None when named but unreadable; [] when
    no report was named. Both pip-audit JSON shapes are read (a bare list of
    dependencies, or ``{"dependencies": [...]}``)."""
    if report is None:
        return []
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    deps = data.get("dependencies") if isinstance(data, dict) else data
    return [d for d in deps or [] if isinstance(d, dict)]


def _vulns(deps) -> list:
    out = []
    for dep in deps or []:
        for vuln in dep.get("vulns") or []:
            if isinstance(vuln, dict) and vuln.get("id"):
                out.append((dep, vuln))
    return out


def measured(report: Path, pip_report=None) -> str:
    """``1`` when the scan ran: every named report exists and parses."""
    if load_results(report) is None or load_dependencies(pip_report) is None:
        return "0"
    return "1"


def verdict(report: Path, pip_report=None) -> str:
    results = load_results(report)
    deps = load_dependencies(pip_report)
    if results is None or deps is None:
        return "0"
    if any(blocking(r) for r in results) or _vulns(deps):
        return "0"
    return "1"


def typed_rows(results, deps=()) -> list:
    out = []
    for r in results or []:
        if not blocking(r):
            continue
        out.append(
            {
                "kind": KIND_FILE,
                "file": str(r.get("filename", "?")),
                "line": r.get("line_number"),
                "test_id": str(r.get("test_id", "?")),
                "severity": str(r.get("issue_severity", "")).upper(),
                "text": str(r.get("issue_text", "")).strip(),
            }
        )
    for dep, vuln in _vulns(deps):
        out.append(
            {
                "kind": KIND_DEPENDENCY,
                "package": str(dep.get("name", "?")),
                "version": str(dep.get("version", "?")),
                "vuln_id": str(vuln.get("id")),
            }
        )
    return out[:EVIDENCE_ROWS]


def row_text(row: dict) -> str:
    if row.get("kind") == KIND_DEPENDENCY:
        return f"{row['vuln_id']} {row['package']}=={row['version']}"
    return f"{row['test_id']} {row['file']}:{row['line'] if row.get('line') is not None else '?'} [{row['severity']}] {row['text']}"


def rows(results) -> list:
    """Display text for the blocking bandit results (kept for callers)."""
    return [row_text(r) for r in typed_rows(results)]


def audit_checks(harness: Path) -> list:
    tree = ast.parse(harness.read_text(encoding="utf-8", errors="replace"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "AUDIT_CHECKS"
        ):
            try:
                return [str(c) for c in ast.literal_eval(node.value)]
            except ValueError:
                return []
    return []


def evidence_rows(harness: Path, report: Path, pip_report=None) -> dict:
    """{audit check: [typed rows]} -- empty when nothing blocks."""
    checks = audit_checks(harness)
    results = load_results(report)
    deps = load_dependencies(pip_report)
    if not checks or results is None:
        return {}
    found = typed_rows(results, deps or [])
    if not found:
        return {}
    return {check: list(found) for check in checks}


def evidence(harness: Path, report: Path, pip_report=None) -> dict:
    """{audit check: display text} -- the same findings as evidence_rows."""
    return {
        check: "\n".join(row_text(r) for r in found)
        for check, found in evidence_rows(harness, report, pip_report).items()
    }


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "measured":
        print(measured(Path(argv[1]), argv[2] if len(argv) > 2 else None))
        return 0
    if len(argv) >= 2 and argv[0] == "verdict":
        print(verdict(Path(argv[1]), argv[2] if len(argv) > 2 else None))
        return 0
    if len(argv) >= 3 and argv[0] == "evidence":
        out_dir = Path(argv[3]) if len(argv) > 3 else Path(".")
        pip_report = argv[4] if len(argv) > 4 else None
        typed = evidence_rows(Path(argv[1]), Path(argv[2]), pip_report)
        if typed:
            text = {check: "\n".join(row_text(r) for r in found) for check, found in typed.items()}
            (out_dir / OUT_NAME).write_text(json.dumps(text, indent=2) + "\n", encoding="utf-8")
            (out_dir / ROWS_OUT_NAME).write_text(json.dumps(typed, indent=2) + "\n", encoding="utf-8")
        print(f"audit evidence: {len(typed)} check(s)")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
