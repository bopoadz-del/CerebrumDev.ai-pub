"""F-8e grade wiring -- the hardwiring section aggregates two-dataset verdicts +
scan hits, gates certification, and is an optional (backward-compatible) field."""
from __future__ import annotations

import pytest

from app.factory.build.product_grader.grade_record import (
    PASS,
    _schema_errors,
    build_grade,
    hardwiring_section,
)
from app.factory.build.product_grader.two_dataset import (
    BOTH_ABSENT,
    HARDWIRED_CONSTANT,
    INPUT_SPECIFIC_BRANCH,
    OK,
    WRONG_COMPUTATION,
)

_FN_OK = {"ok": True}
_UI_OK = {"ok": True}
_L2_PASS = {"ran": True, "verdicts": [{"question": "q", "verdict": PASS, "evidence": "shown"}]}


@pytest.mark.parametrize("verdicts, clean", [
    ({"a": OK, "b": BOTH_ABSENT}, True),
    ({"a": HARDWIRED_CONSTANT}, False),
    ({"a": INPUT_SPECIFIC_BRANCH, "b": OK}, False),
    ({"a": WRONG_COMPUTATION}, False),
])
def test_hardwiring_section_flags_fail_verdicts(verdicts, clean):
    sec = hardwiring_section(verdicts, {}, [])
    assert sec["ran"] is True
    assert sec["clean"] is clean
    assert bool(sec["findings"]) is (not clean)


@pytest.mark.parametrize("hits, leaks, clean", [
    ({}, [], True),
    ({"app/actions/x.py": ["acme_corp"]}, [], False),
    ({}, ["acme_corp"], False),
    ({"app/actions/x.py": ["acme_corp"]}, ["zephyr"], False),
])
def test_hardwiring_section_flags_scan_hits(hits, leaks, clean):
    sec = hardwiring_section({"a": OK}, hits, leaks)
    assert sec["clean"] is clean


@pytest.mark.parametrize("clean, certified", [(True, True), (False, False)])
def test_build_grade_gates_certified_on_hardwiring(clean, certified):
    hw = {
        "ran": True, "clean": clean,
        "findings": [] if clean else [{"kind": "function", "capability": "a", "verdict": HARDWIRED_CONSTANT}],
    }
    g = build_grade(product_id="p", functions=_FN_OK, ui=_UI_OK, layer2=_L2_PASS, hardwiring=hw)
    assert g["certified"] is certified
    assert g["hardwiring"] == hw
    assert _schema_errors(g) == []
    if not clean:
        assert any("de-hardwire a" in t for t in g["next_tasks"])


@pytest.mark.parametrize("product_id", ["p1", "p2"])
def test_build_grade_without_hardwiring_is_backward_compatible(product_id):
    g = build_grade(product_id=product_id, functions=_FN_OK, ui=_UI_OK, layer2=_L2_PASS)
    assert "hardwiring" not in g
    assert g["certified"] is True
    assert _schema_errors(g) == []


@pytest.mark.parametrize("bad", [
    {"ran": "yes", "clean": True, "findings": []},     # ran not a bool
    {"ran": True, "clean": True, "findings": "nope"},   # findings not a list
])
def test_schema_rejects_a_malformed_hardwiring_section(bad):
    g = build_grade(product_id="p", functions=_FN_OK, ui=_UI_OK, layer2=_L2_PASS)
    g["hardwiring"] = bad
    assert any("hardwiring" in e for e in _schema_errors(g))
