"""F-8e -- two-dataset build check (deterministic core).

Invented definition (kernel-row shape); no real product/client/vertical.
"""
from __future__ import annotations

import pytest

import app.factory.build.product_grader.two_dataset as two_dataset
import app.factory.build.product_grader.vault as vault  # noqa: F401  (CI-M module token)

DEF = {
    "id": "net_total",
    "key": "universal:net_total_v1",
    "expression": "base * rate - discount",
    "inputs": ["base", "rate", "discount"],
}
SCHEMA = {
    "base": {"minimum": 10, "maximum": 500},
    "rate": {"minimum": 1, "maximum": 3},
    "discount": {"minimum": 0, "maximum": 50},
}


@pytest.mark.parametrize("sa, sb", [(1, 2), (7, 99)])
def test_build_datasets_pairs_two_distinct_seeds(sa, sb):
    resolve = lambda cid: {"net_total": DEF}.get(cid)  # noqa: E731
    pairs = two_dataset.build_datasets(["net_total", "uncovered"], resolve, {"net_total": SCHEMA}, sa, sb)
    assert [p.capability_id for p in pairs] == ["net_total"]  # kernel-gap cap omitted
    p = pairs[0]
    assert p.inputs_a != p.inputs_b  # two seeds -> two input sets
    assert p.expected_a == pytest.approx(p.inputs_a["base"] * p.inputs_a["rate"] - p.inputs_a["discount"])
    assert p.expected_b == pytest.approx(p.inputs_b["base"] * p.inputs_b["rate"] - p.inputs_b["discount"])


@pytest.mark.parametrize("oa, ob, ea, eb, want", [
    (5.0, 5.0, 1.0, 2.0, two_dataset.HARDWIRED_CONSTANT),     # identical out on diff inputs
    (1.0, None, 1.0, 2.0, two_dataset.INPUT_SPECIFIC_BRANCH),  # present A, absent B
    (9.0, 9.9, 1.0, 2.0, two_dataset.WRONG_COMPUTATION),      # differ but != kernel expected
    (1.0, 2.0, 1.0, 2.0, two_dataset.OK),                     # differ, == kernel expected
])
def test_verdict_covers_every_outcome(oa, ob, ea, eb, want):
    # Also kills a "compare A to A" mutant: it would make OK/WRONG look hardwired.
    assert two_dataset.verdict(oa, ob, ea, eb) == want


@pytest.mark.parametrize("ea, eb", [(1.0, 2.0), (3.0, 4.0)])
def test_verdict_both_absent_is_not_gradable(ea, eb):
    assert two_dataset.verdict(None, None, ea, eb) == two_dataset.BOTH_ABSENT


@pytest.mark.parametrize("oa, ob", [(1.0, 2.0), (3.0, 7.0)])
def test_differ_with_no_kernel_expected_is_ok(oa, ob):
    # No kernel prediction (gap): differing outputs are enough; can't check computation.
    assert two_dataset.verdict(oa, ob, None, None) == two_dataset.OK
