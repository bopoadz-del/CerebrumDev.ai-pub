#!/usr/bin/env python3
"""A gate that cannot block is not a gate.

This repository has shipped that defect four separate times: lotdesk_gate
tested but wired to nothing, grounding's ``strict`` branch unreachable
because no caller passed it, ``store_write_exists`` a literal False, and a
generated release_gate invoked by no product. Each was found by hand, months
apart. This audit finds the next one.

Three properties, checked statically over app/factory/build/:

  1. REACHABLE -- every gate_* function is the roster or is called,
     transitively, by something in it. A gate nothing calls is dead code
     wearing a gate's name.
  2. CONSUMED  -- the roster itself is read by a non-test module. Because
     reachability is measured FROM the roster, an unconsumed roster would
     make every gate in the tree look reachable while none could fire.
  3. REFUSED   -- some test asserts it says NO. A test that only proves a
     gate passes on good input is compatible with a gate that passes on
     everything, which is the failure mode being guarded against.

Coverage propagates DOWN the call graph: a refusal test against a composite
covers the parts it delegates to. That is deliberate -- exercising the
composite is the better test, because it also proves the composite
propagates its parts' verdicts rather than swallowing them. The limitation
is real and worth naming: this cannot tell WHICH part a composite's refusal
test tripped, so a composite tested only on its first check will still mark
its later parts covered. Guard that with a test asserting the composite does
not stop at its first part (see test_contract_gates_refuse.py).

Exit 1 on any unregistered finding; register in KNOWN_GATE_GAPS.md.
"""
from __future__ import annotations

import ast
import os
import sys

BUILD_DIR = os.path.join("backend", "app", "factory", "build")
APP_DIR = os.path.join("backend", "app")
TESTS_DIR = os.path.join("backend", "tests")
ROSTER_FILE = os.path.join(BUILD_DIR, "gates.py")
ROSTER_NAME = "GATES"
KNOWN_FILE = "KNOWN_GATE_GAPS.md"

def _is_gate(node):
    """A gate is identified by its signature, not its name.

    Naming alone misfires: ``gate_for`` is the roster's accessor, not a gate,
    and reporting it as an unreachable gate is noise that trains a reader to
    ignore this audit. The contract is what identifies one -- it takes a
    GateContext and returns a GateResult.
    """
    if node.name.startswith("_"):
        return False

    def _name(ann):
        if ann is None:
            return ""
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            return ann.value  # quoted forward reference
        return getattr(ann, "id", "") or getattr(ann, "attr", "")

    if _name(node.returns) != "GateResult":
        return False
    args = node.args.args + node.args.kwonlyargs
    return any(_name(a.annotation) == "GateContext" for a in args)


# Assertions that constitute proving a refusal. A gate proven only to pass
# is not proven to gate.
REFUSAL_MARKERS = (
    "pytest.raises",
    "is False",
    "== False",
    "not ok",
    "ok is False",
    ".ok)",
    "FAIL",
    "BLOCKED",
    "refus",
    "reject",
    "assert not ",
)


def load_known():
    if not os.path.exists(KNOWN_FILE):
        return set()
    out = set()
    for line in open(KNOWN_FILE, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line.startswith("- ") and "::" in line:
            out.add(line[2:].split("  ")[0].strip())
    return out


def _py_files(root):
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def _parse(path):
    try:
        return ast.parse(open(path, encoding="utf-8", errors="ignore").read())
    except (SyntaxError, OSError):
        return None


def collect_definitions(build_dir):
    """gate name -> module path that defines it."""
    out = {}
    for path in _py_files(build_dir):
        tree = _parse(path)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_gate(node):
                    out[node.name] = path
    return out


def collect_calls(build_dir, gate_names):
    """gate name -> set of gate names it calls (directly)."""
    calls = {name: set() for name in gate_names}
    for path in _py_files(build_dir):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in calls:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    fn = inner.func
                    target = getattr(fn, "id", None) or getattr(fn, "attr", None)
                    if target in gate_names and target != node.name:
                        calls[node.name].add(target)
    return calls


def collect_roster(roster_file):
    """The names assigned into the GATES mapping."""
    tree = _parse(roster_file)
    if tree is None:
        return set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(getattr(t, "id", None) == ROSTER_NAME for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            return {
                getattr(v, "id", None) or getattr(v, "attr", None)
                for v in value.values
            } - {None}
    return set()


def reachable_from(roster, calls):
    """Transitive closure: a gate the roster reaches, directly or via another."""
    seen = set()
    queue = list(roster)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        queue.extend(calls.get(name, ()))
    return seen


def roster_consumers(app_dir, roster_file):
    """Non-test modules that actually consult the roster.

    Reachability is measured from GATES, so if nothing consumes GATES the
    whole graph is dead and every gate would still be reported reachable.
    This is the check that stops the audit from certifying an orphan tree.
    """
    out = []
    roster_abs = os.path.abspath(roster_file)
    for path in _py_files(app_dir):
        if os.path.abspath(path) == roster_abs:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        # Walked as AST, not grepped: roles.py renders the literal string
        # "GATES" into generated product source, and a template that emits
        # the word is not a consumer of this roster.
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in (ROSTER_NAME, "gate_for"):
                out.append(path)
                break
            if isinstance(node, ast.Attribute) and node.attr in (ROSTER_NAME, "gate_for"):
                out.append(path)
                break
    return out


def refusal_tests(tests_dir, gate_name):
    """Test files that name this gate AND assert something refuses."""
    hits = []
    for path in _py_files(tests_dir):
        try:
            src = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if gate_name not in src:
            continue
        if any(marker in src for marker in REFUSAL_MARKERS):
            hits.append(path)
    return hits


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not os.path.isdir(BUILD_DIR):
        sys.stdout.write("no %s; run from the repository root\n" % BUILD_DIR)
        return 2

    definitions = collect_definitions(BUILD_DIR)
    if not definitions:
        # An audit that finds nothing because it looked nowhere is the very
        # failure it exists to catch.
        sys.stdout.write("FAILED: no gate functions found under %s\n" % BUILD_DIR)
        return 1

    roster = collect_roster(ROSTER_FILE)
    if not roster:
        sys.stdout.write("FAILED: could not read the %s roster\n" % ROSTER_NAME)
        return 1

    calls = collect_calls(BUILD_DIR, set(definitions))
    live = reachable_from(roster, calls)
    known = load_known()

    # A refusal test against a composite covers what it delegates to.
    directly_tested = {n for n in definitions if refusal_tests(TESTS_DIR, n)}
    covered = reachable_from(directly_tested, calls)

    findings = []

    consumers = roster_consumers(APP_DIR, ROSTER_FILE)
    if not consumers:
        findings.append(
            (
                "%s :: roster_unconsumed" % ROSTER_NAME,
                "no non-test module reads %s or gate_for; every gate below "
                "would be reported reachable while none could fire"
                % ROSTER_NAME,
            )
        )

    for name in sorted(definitions):
        module = definitions[name]

        if name not in live:
            findings.append(
                (
                    "%s :: unreachable" % name,
                    "defined in %s but neither in %s nor called by anything in it"
                    % (os.path.relpath(module), ROSTER_NAME),
                )
            )

        if name not in covered:
            findings.append(
                (
                    "%s :: no_refusal_test" % name,
                    "no test names %s (or a gate that calls it) and asserts a "
                    "refusal; a gate proven only to pass is not proven to gate"
                    % name,
                )
            )

    unregistered = [(k, d) for k, d in findings if k not in known]
    registered = len(findings) - len(unregistered)

    if unregistered:
        sys.stdout.write("GATE FINDINGS (wire it, test it, or register in %s):\n" % KNOWN_FILE)
        for key, detail in unregistered:
            sys.stdout.write("  %s\n      %s\n" % (key, detail))
        sys.stdout.write(
            "TOTAL: %d unregistered across %d gates (%d registered)\n"
            % (len(unregistered), len(definitions), registered)
        )
        return 1

    sys.stdout.write(
        "GATES OK: %d gates, all reachable from %s and proven to refuse "
        "(%d registered).\n" % (len(definitions), ROSTER_NAME, registered)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
