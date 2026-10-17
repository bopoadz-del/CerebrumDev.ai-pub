"""The gate audit must itself be shown to refuse.

An audit that passes because it stopped looking is the same defect it was
written to find, one level up.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "audit_gates.py"


def _load():
    spec = importlib.util.spec_from_file_location("audit_gates", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load()

GATE_SRC = '''
from app.factory.build.gates import GateContext, GateResult

def gate_alpha(ctx: GateContext) -> GateResult:
    return GateResult(ok=True, gate="alpha", detail="")

def gate_beta(ctx: GateContext) -> GateResult:
    return GateResult(ok=True, gate="beta", detail="")

def gate_for(role) -> "Gate":
    return GATES[role]

GATES = {
    Role.A: gate_alpha,
}
'''


def _tree(tmp_path: Path, *, gates_src=GATE_SRC, tests_src="", consumer=True):
    build = tmp_path / "backend" / "app" / "factory" / "build"
    build.mkdir(parents=True)
    (build / "gates.py").write_text(gates_src, encoding="utf-8")

    app = tmp_path / "backend" / "app"
    if consumer:
        (app / "runner.py").write_text(
            "from app.factory.build.gates import gate_for\n"
            "def run(role):\n    return gate_for(role)\n",
            encoding="utf-8",
        )

    tests = tmp_path / "backend" / "tests"
    tests.mkdir(parents=True)
    if tests_src:
        (tests / "test_x.py").write_text(tests_src, encoding="utf-8")
    return tmp_path


@pytest.fixture
def at(monkeypatch):
    """Point the audit at a synthetic tree."""

    def _use(root: Path):
        monkeypatch.chdir(root)
        return audit

    return _use


def test_a_gate_nothing_calls_is_reported(at, tmp_path):
    """gate_beta is defined but is neither rostered nor called."""
    at(_tree(tmp_path, tests_src="assert x.ok is False\ngate_alpha\ngate_beta\n"))
    assert audit.main([]) == 1


def test_a_rostered_gate_is_reachable(at, tmp_path):
    src = GATE_SRC.replace(
        "def gate_beta(ctx: GateContext) -> GateResult:\n"
        '    return GateResult(ok=True, gate="beta", detail="")\n',
        "",
    )
    at(_tree(tmp_path, gates_src=src, tests_src="gate_alpha\nassert r.ok is False\n"))
    assert audit.main([]) == 0


def test_coverage_propagates_from_a_composite_to_its_parts(at, tmp_path):
    """Testing the composite is the better test; it must count for the parts.

    gate_beta has no test of its own -- only gate_alpha, which calls it.
    """
    src = '''
from app.factory.build.gates import GateContext, GateResult

def gate_beta(ctx: GateContext) -> GateResult:
    return GateResult(ok=True, gate="beta", detail="")

def gate_alpha(ctx: GateContext) -> GateResult:
    inner = gate_beta(ctx)
    if not inner.ok:
        return inner
    return GateResult(ok=True, gate="alpha", detail="")

def gate_for(role):
    return GATES[role]

GATES = {
    Role.A: gate_alpha,
}
'''
    at(_tree(tmp_path, gates_src=src, tests_src="gate_alpha\nassert r.ok is False\n"))
    assert audit.main([]) == 0


def test_a_gate_with_no_refusal_test_is_reported(at, tmp_path):
    src = GATE_SRC.replace(
        "def gate_beta(ctx: GateContext) -> GateResult:\n"
        '    return GateResult(ok=True, gate="beta", detail="")\n',
        "",
    )
    # names the gate, but only ever asserts it passes
    at(_tree(tmp_path, gates_src=src, tests_src="gate_alpha\nassert r.ok is True\n"))
    assert audit.main([]) == 1


def test_an_unconsumed_roster_is_reported(at, tmp_path):
    """Reachability is measured FROM the roster. If nothing reads the roster,
    every gate would be reported reachable while none could fire."""
    src = GATE_SRC.replace(
        "def gate_beta(ctx: GateContext) -> GateResult:\n"
        '    return GateResult(ok=True, gate="beta", detail="")\n',
        "",
    )
    at(_tree(tmp_path, gates_src=src, tests_src="gate_alpha\nassert r.ok is False\n",
             consumer=False))
    assert audit.main([]) == 1


def test_the_roster_accessor_is_not_counted_as_a_gate(at, tmp_path):
    """gate_for reads the roster; it is not a gate, and reporting it as an
    unreachable one is noise that trains a reader to ignore this audit."""
    at(_tree(tmp_path))
    defs = audit.collect_definitions(os.path.join("backend", "app", "factory", "build"))
    assert "gate_for" not in defs
    assert {"gate_alpha", "gate_beta"} <= set(defs)


def test_an_empty_tree_fails_rather_than_passing_vacuously(at, tmp_path):
    build = tmp_path / "backend" / "app" / "factory" / "build"
    build.mkdir(parents=True)
    (build / "gates.py").write_text("GATES = {}\n", encoding="utf-8")
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    at(tmp_path)
    assert audit.main([]) == 1, "an audit that finds nothing must not report success"


def test_registration_suppresses_a_finding(at, tmp_path):
    root = _tree(tmp_path, tests_src="assert x.ok is False\ngate_alpha\ngate_beta\n")
    (root / "KNOWN_GATE_GAPS.md").write_text(
        "- gate_beta :: unreachable  registered\n", encoding="utf-8"
    )
    at(root)
    assert audit.main([]) == 0


def test_this_repository_passes_the_audit(monkeypatch):
    monkeypatch.chdir(REPO)
    assert audit.main([]) == 0, "run: python scripts/audit_gates.py"
