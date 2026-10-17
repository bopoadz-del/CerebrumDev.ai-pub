"""Domain Intelligence Compiler — scout a donor repository.

Discovers candidate formulas, rules, workflows and approval policies with
file/line evidence. Discovery is by code SHAPE read from the AST -- what a
function returns and how a constant is built -- never by the words in a
name, comment or docstring, so a donor is scouted the same whatever
vocabulary it is written in:

* formula  -- a public function that returns an arithmetic result;
* approval -- a public function that returns a boolean decision;
* workflow -- a public function that chooses its result among constant
  state values by condition;
* rule     -- an UPPER-case module constant that is a table of records. The scout NEVER certifies: every discovered artifact is marked
``candidate`` with its provenance, and the discovery report separates
VERIFIED (test evidence found) from UNVERIFIED.

This is a discovery tool, not a certifier — certification states move
only through the STORE_MANAGER / domain-review gates.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Arithmetic operators: a function that returns one of these computes a value.
_ARITHMETIC = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def _returns(node: Any) -> List[ast.expr]:
    """The expressions a function returns (nested defs excluded)."""
    out: List[ast.expr] = []
    stack = list(node.body)
    while stack:
        cur = stack.pop()
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(cur, ast.Return) and cur.value is not None:
            out.append(cur.value)
        stack.extend(ast.iter_child_nodes(cur))
    return out


def _computes(expr: ast.expr) -> bool:
    """The returned value is built by arithmetic."""
    return any(
        isinstance(n, ast.BinOp) and isinstance(n.op, _ARITHMETIC) for n in ast.walk(expr)
    )


def _decides(expr: ast.expr) -> bool:
    """The returned value is a boolean decision (a comparison or its negation/combination)."""
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return True
    if isinstance(expr, ast.BoolOp):
        return all(_decides(v) for v in expr.values)
    return isinstance(expr, ast.Compare)


def _state_leaves(expr: ast.expr) -> List[ast.expr]:
    """Leaves of a conditional choice (a if c else b), else the expression."""
    if isinstance(expr, ast.IfExp):
        return _state_leaves(expr.body) + _state_leaves(expr.orelse)
    return [expr]


def _chooses_a_state(returns: List[ast.expr]) -> bool:
    """The function picks its result among constant state values by condition:
    either one conditional expression or several returns, at least one a str constant."""
    leaves = [leaf for r in returns for leaf in _state_leaves(r)]
    branching = len(returns) > 1 or any(isinstance(r, ast.IfExp) for r in returns)
    return branching and any(
        isinstance(leaf, ast.Constant) and isinstance(leaf.value, str) for leaf in leaves
    )


def _is_record_table(value: ast.expr) -> bool:
    """A list of two-or-more-field dicts with str keys -- a table of records."""
    if not isinstance(value, ast.List) or not value.elts:
        return False
    for elt in value.elts:
        if not isinstance(elt, ast.Dict) or len(elt.keys) < 2:
            return False
        if not all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in elt.keys):
            return False
    return True


def _called_names(tree: ast.AST) -> List[str]:
    """Names a parsed module calls: f(...) and obj.f(...)."""
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.append(node.func.attr)
    return names


@dataclass
class Discovery:
    """One discovered candidate artifact with exact provenance."""

    kind: str  # formula | rule | workflow | approval
    symbol: str
    path: str
    line: int
    donor_commit: str
    test_evidence: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "path": self.path,
            "line": self.line,
            "donor_commit": self.donor_commit,
            "test_evidence": list(self.test_evidence),
            "classification": "VERIFIED" if self.test_evidence else "EXECUTABLE_UNVERIFIED",
            "notes": self.notes,
        }


@dataclass
class ScoutReport:
    repo: str
    commit: str
    discoveries: List[Discovery] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "commit": self.commit,
            "discoveries": [d.to_dict() for d in self.discoveries],
            "totals": {
                kind: sum(1 for d in self.discoveries if d.kind == kind)
                for kind in ("formula", "rule", "workflow", "approval")
            },
        }


class DonorScout:
    """Scan a donor checkout for reasoning artifacts. Read-only."""

    def __init__(self, repo_root: Path, commit: str = "") -> None:
        self.root = Path(repo_root)
        self.commit = commit
        self._test_index: Dict[str, List[str]] = {}

    def _build_test_index(self) -> None:
        tests_dir = self.root / "tests"
        if not tests_dir.is_dir():
            tests_dir = self.root / "test"
        if not tests_dir.is_dir():
            return
        for test_file in sorted(tests_dir.rglob("*.py")):
            try:
                text = test_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            rel = str(test_file.relative_to(self.root)).replace("\\", "/")
            for symbol in _called_names(tree):
                self._test_index.setdefault(symbol, []).append(rel)

    def scout(self, paths: Optional[List[str]] = None) -> ScoutReport:
        self._build_test_index()
        report = ScoutReport(repo=str(self.root), commit=self.commit)
        candidates = paths or ["backend/app", "app", "src"]
        for base in candidates:
            root = self.root / base
            if not root.is_dir():
                continue
            for py_file in sorted(root.rglob("*.py")):
                if "__pycache__" in py_file.parts:
                    continue
                try:
                    text = py_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(py_file.relative_to(self.root)).replace("\\", "/")
                report.discoveries.extend(self._scan_file(text, rel))
        return report

    def _scan_file(self, text: str, rel: str) -> List[Discovery]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        out: List[Discovery] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.extend(self._classify_function(node, rel))
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(
                    node.targets[0], ast.Name
                ):
                    continue
                name = node.targets[0].id
                if not name.isupper() or not _is_record_table(node.value):
                    continue
                out.append(
                    Discovery(
                        kind="rule",
                        symbol=name,
                        path=rel,
                        line=node.lineno,
                        donor_commit=self.commit,
                        test_evidence=self._test_index.get(name, []),
                        notes="record-table constant — candidate, review before extraction",
                    )
                )
        return out

    def _classify_function(
        self, node: Any, rel: str
    ) -> List[Discovery]:
        name = node.name
        if name.startswith("_"):
            return []
        returns = _returns(node)
        evidence = self._test_index.get(name, [])
        kinds = []
        if any(_computes(r) for r in returns):
            kinds.append("formula")
        if returns and all(_decides(r) for r in returns):
            kinds.append("approval")
        if _chooses_a_state(returns):
            kinds.append("workflow")
        return [
            Discovery(
                kind=kind,
                symbol=name,
                path=rel,
                line=node.lineno,
                donor_commit=self.commit,
                test_evidence=evidence,
            )
            for kind in kinds
        ]
