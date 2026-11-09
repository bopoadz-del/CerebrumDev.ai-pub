"""Coverage-derived test mapping: build_mapping, load_mapping, and the
mutation gate's mapping-first tests_for (advisor 23:12Z)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "scripts" / "mutation_gate.py").is_file())

spec = importlib.util.spec_from_file_location("coverage_test_map", ROOT / "scripts" / "coverage_test_map.py")
cm = importlib.util.module_from_spec(spec)
sys.modules["coverage_test_map"] = cm
spec.loader.exec_module(cm)

gspec = importlib.util.spec_from_file_location("mutation_gate", ROOT / "scripts" / "mutation_gate.py")
mg = importlib.util.module_from_spec(gspec)
sys.modules["mutation_gate"] = mg
gspec.loader.exec_module(mg)


@pytest.mark.parametrize(
    "executions,want",
    [
        # one function covered by two files -> files sorted
        (
            [("tests/test_b.py", "s/a.py", "fee"), ("tests/test_a.py", "s/a.py", "fee")],
            {"s/a.py": {"fee": ["tests/test_a.py", "tests/test_b.py"]}},
        ),
        # two functions in one module, functions sorted
        (
            [("tests/test_a.py", "s/a.py", "fee"), ("tests/test_a.py", "s/a.py", "execute")],
            {"s/a.py": {"execute": ["tests/test_a.py"], "fee": ["tests/test_a.py"]}},
        ),
        # a duplicate triple collapses to one file
        (
            [("tests/test_a.py", "s/a.py", "fee"), ("tests/test_a.py", "s/a.py", "fee")],
            {"s/a.py": {"fee": ["tests/test_a.py"]}},
        ),
    ],
)
def test_build_mapping_collapses_dedupes_and_sorts(executions, want):
    assert cm.build_mapping(executions) == want


@pytest.mark.parametrize(
    "content,want",
    [
        (None, {}),  # missing file
        ("{bad json", {}),  # JSON decode error
        ('"not a dict"', {}),  # valid JSON, wrong shape
        ('{"s/a.py": {"fee": ["tests/test_a.py"]}}', {"s/a.py": {"fee": ["tests/test_a.py"]}}),
    ],
)
def test_load_mapping_tolerates_absence_and_corruption(tmp_path, content, want):
    if content is not None:
        (tmp_path / "m.json").write_text(content, "utf-8")
    assert cm.load_mapping(tmp_path, "m.json") == want


@pytest.mark.parametrize(
    "mapping,want",
    [
        ({"s/calc.py": {"fee": ["tests/test_a.py"]}}, ["tests/test_a.py"]),
        (
            {"s/calc.py": {"fee": ["tests/test_b.py", "tests/test_a.py"]}},
            ["tests/test_a.py", "tests/test_b.py"],  # direct-reference file first
        ),
    ],
)
def test_tests_for_prefers_the_mapping_entry(tmp_path, mapping, want):
    """A coverage-derived entry is used, ordered so the file that names the
    function runs first (so -x stops at the likely killer)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "from s.calc import fee\n\ndef test_fee():\n    fee(1)\n", "utf-8"
    )
    (tmp_path / "tests" / "test_b.py").write_text("def test_other(): pass\n", "utf-8")
    got = mg.tests_for("s/calc.py", "fee", tmp_path, ["tests"], ["src"], mapping)
    assert got == want


@pytest.mark.parametrize(
    "src_file,fn_name,test_body",
    [
        ("src/calc.py", "fee", "from src.calc import fee\n\ndef test_x():\n    fee(1)\n"),
        ("src/other.py", "lonely", "import src.other\n\ndef test_x():\n    src.other.lonely(1)\n"),
    ],
)
def test_tests_for_falls_back_to_static_when_unmapped(tmp_path, src_file, fn_name, test_body):
    """A function absent from the map still gets the static module/name match,
    so a mapping never silently shrinks coverage below the old behavior."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def fee(x):\n    return x\n", "utf-8")
    (tmp_path / "src" / "other.py").write_text("def lonely(x):\n    return x\n", "utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text(test_body, "utf-8")
    mapping = {src_file: {"not_" + fn_name: ["tests/test_calc.py"]}}
    got = mg.tests_for(src_file, fn_name, tmp_path, ["tests"], ["src"], mapping)
    assert got == ["tests/test_calc.py"]
