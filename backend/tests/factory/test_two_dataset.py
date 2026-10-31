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


def _workspace(tmp_path, handles):
    ws = tmp_path / "ws"
    (ws / "app" / "actions").mkdir(parents=True)
    (ws / "app" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "app" / "actions" / "__init__.py").write_text("", encoding="utf-8")
    for cid, body in handles.items():
        (ws / "app" / "actions" / f"{cid}.py").write_text(body, encoding="utf-8")
    return ws


_GOOD = "def handle(p):\n    return {'result': p['base'] * p['rate'] - p['discount']}\n"
_HARDWIRED = "def handle(p):\n    return {'result': 42}\n"
_WRONG = "def handle(p):\n    return {'result': p['base'] * p['rate']}\n"  # forgets discount
_SPECIFIC = "def handle(p):\n    if p['base'] > 1000:\n        return {'result': p['base']}\n    return None\n"


@pytest.mark.parametrize("ia, ib", [
    ({"base": 100.0, "rate": 2.0, "discount": 10.0}, {"base": 50.0, "rate": 3.0, "discount": 5.0}),
    ({"base": 2000.0, "rate": 1.0, "discount": 1.0}, {"base": 10.0, "rate": 2.0, "discount": 2.0}),
])
def test_run_two_dataset_end_to_end_verdicts(ia, ib, tmp_path):
    ws = _workspace(tmp_path, {"good": _GOOD, "hardwired": _HARDWIRED, "wrong": _WRONG, "specific": _SPECIFIC})
    ea = ia["base"] * ia["rate"] - ia["discount"]
    eb = ib["base"] * ib["rate"] - ib["discount"]
    pairs = [
        two_dataset.DatasetPair("good", ia, ea, ib, eb),
        two_dataset.DatasetPair("hardwired", ia, ea, ib, eb),
        two_dataset.DatasetPair("wrong", ia, ea, ib, eb),
        two_dataset.DatasetPair("specific", {"base": 2000.0, "rate": 1.0, "discount": 0.0}, 2000.0,
                                {"base": 5.0, "rate": 1.0, "discount": 0.0}, 5.0),
    ]
    v = {c.capability_id: c.verdict for c in two_dataset.run_two_dataset(pairs, ws)}
    assert v["good"] == two_dataset.OK
    assert v["hardwired"] == two_dataset.HARDWIRED_CONSTANT
    assert v["wrong"] == two_dataset.WRONG_COMPUTATION
    assert v["specific"] == two_dataset.INPUT_SPECIFIC_BRANCH
