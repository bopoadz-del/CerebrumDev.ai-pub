"""The stub audit must not flag Protocol interface bodies as hollow stubs."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit_stubs.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("audit_stubs_under_test", AUDIT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_protocol_methods_are_not_hollow_stubs():
    audit = _load_audit()
    source = (
        "from typing import Protocol\n"
        "class Store(Protocol):\n"
        "    def upsert(self, x) -> None: ...\n"
        "    def query(self, x) -> list: ...\n"
        "class Real:\n"
        "    def hollow(self) -> None:\n"
        "        pass\n"
    )
    tree = ast.parse(source)
    skipped = audit.protocol_methods(tree)
    assert {name for _, name in skipped} == {"upsert", "query"}
    # The hollow Real.hollow is NOT protocol-skipped — the audit still catches it.
    assert "hollow" not in {name for _, name in skipped}


def test_protocol_detection_shapes():
    audit = _load_audit()
    tree = ast.parse(
        "import typing\n"
        "class A(typing.Protocol):\n"
        "    def f(self) -> None: ...\n"
        "class B:\n"
        "    def g(self) -> None: ...\n"
    )
    skipped = audit.protocol_methods(tree)
    assert {(name) for _, name in skipped} == {"f"}
