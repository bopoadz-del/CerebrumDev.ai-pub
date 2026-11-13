"""Option (b), Advisor 13:24Z: a NOT_CERTIFIED round carries the grader's
reason CLASS per failing item, derived from the grade record alone.

Nothing in this file names a real product, client, vertical or capability:
the ids are invented, and the "product text" planted in each record is
there only to prove it never reaches a reason.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from app.factory.build import grader
from app.factory.build.product_grader.function_grader import NO_INPUT_CONTRACT, ONE_RESULT

PRODUCT_TEXT = ("Clientco", "secret", "Secret", "handle raised", "brief asked")


def _fn(cid, *, ok=False, two_input=False, survived=(), detail=""):
    return {
        "capability_id": cid,
        "two_input_passed": two_input,
        "mutants_killed": [],
        "mutants_survived": list(survived),
        "detail": detail,
        "ok": ok,
    }


def _grade(functions, ui, layer2, hardwiring=None, ungraded=()):
    grade = {
        "layer1": {"functions": {"functions": functions, "ungraded": list(ungraded)}, "ui": ui},
        "layer2": layer2,
    }
    if hardwiring is not None:
        grade["hardwiring"] = {"ran": True, "clean": not hardwiring, "findings": hardwiring}
    return grade


RECORD_A = (
    ["zorblat_sum", "plinket_rate", "quarm_index", "vexel_total"],
    _grade(
        [
            _fn("zorblat_sum", ok=True, two_input=True),
            _fn("plinket_rate", two_input=True, survived=["field_dependence"]),
            _fn("quarm_index", detail=ONE_RESULT),
        ],
        {
            "ok": False,
            "ran": True,
            "steps": [
                {"name": "load:/", "kind": "load", "target": "/", "passed": True},
                {"name": "route:/v1/zorblat_sum", "kind": "route", "target": "/v1/zorblat_sum", "passed": False},
            ],
        },
        {
            "ran": True,
            "open_fails": ["Does the product deliver capability 'quarm_index' as the brief asked: Clientco secret?"],
        },
        [{"kind": "function", "capability": "plinket_rate", "verdict": "wrong computation"}],
    ),
    [
        ("plinket_rate", "stub_handler", "function"),
        ("quarm_index", "stub_handler", "function"),
        ("vexel_total", "missing_route", "function"),
        ("zorblat_sum", "missing_route", "ui"),
        ("plinket_rate", "wrong_computation", "hardwiring"),
        ("quarm_index", "layer2_fail", "layer2"),
    ],
)

RECORD_B = (
    ["glim_score", "trell_mean"],
    _grade(
        [
            _fn("glim_score", detail="handle raised: KeyError: 'Clientco secret'"),
            _fn("trell_mean", ok=True, two_input=True),
        ],
        {
            "ok": False,
            "ran": True,
            "steps": [
                {"name": "honest_error", "kind": "honest_error", "target": "/v1/glim_score", "passed": False},
                {"name": "screen:secret", "kind": "screen", "target": "/#/Secret-dash", "passed": False},
                {"name": "route:/v1/trell_mean", "kind": "route", "target": "/v1/trell_mean", "passed": True},
            ],
        },
        {"ran": True, "open_fails": ["Does screen 'Secret Dash' render the capability it is declared for?"]},
        [
            {"kind": "function", "capability": "trell_mean", "verdict": "hardwired constant"},
            {"kind": "function", "capability": "glim_score", "verdict": "input-specific branch"},
            {"kind": "forbidden_literal", "file": "app/secret.py", "literals": ["Clientco"]},
        ],
    ),
    [
        ("glim_score", "handler_error", "function"),
        ("glim_score", "wrong_envelope", "ui"),
        ("-", "failed_ui_step", "ui"),
        ("trell_mean", "stub_handler", "hardwiring"),
        ("glim_score", "hardwired_value", "hardwiring"),
        ("-", "hardwired_value", "hardwiring"),
        ("-", "layer2_fail", "layer2"),
    ],
)

RECORD_UNGRADED = (
    ["zorblat_sum"],
    _grade(
        [_fn("zorblat_sum", ok=True, two_input=True)],
        {"ok": False, "ran": False, "failing": ["UI run did not happen: Secret"], "steps": []},
        {"ran": False, "verdicts": [], "open_fails": ["grader refused: Clientco secret"]},
    ),
    [("-", "ungraded", "ui"), ("-", "ungraded", "layer2")],
)


#: A capability with no declared input contract is ungraded (the Factory
#: owes its spec), never a stub, a handler error or a missing route.
RECORD_NO_CONTRACT = (
    ["zorblat_sum", "plinket_rate", "quarm_index"],
    _grade(
        [_fn("zorblat_sum", ok=True, two_input=True), _fn("quarm_index", detail=ONE_RESULT)],
        {"ok": True, "ran": True, "steps": []},
        {"ran": True, "open_fails": []},
        ungraded=[_fn("plinket_rate", detail=NO_INPUT_CONTRACT)],
    ),
    [("quarm_index", "stub_handler", "function"), ("plinket_rate", "ungraded", "function")],
)


@pytest.mark.parametrize(
    "cids, grade, expected",
    [RECORD_A, RECORD_B, RECORD_UNGRADED, RECORD_NO_CONTRACT],
    ids=["mix_a", "mix_b", "ungraded", "no_contract"],
)
def test_reason_classes_count_each_mix_and_carry_no_product_text(cids, grade, expected):
    reasons = grader.reason_classes(grade, cids)
    assert [(r["capability"], r["class"], r["layer"]) for r in reasons] == expected
    assert Counter(r["class"] for r in reasons) == Counter(cls for _cid, cls, _layer in expected)
    assert {r["class"] for r in reasons} <= grader.REASON_CLASSES
    assert all(r["capability"] in set(cids) | {"-"} for r in reasons)
    flat = json.dumps(reasons)
    for text in PRODUCT_TEXT:
        assert text not in flat


@pytest.mark.parametrize(
    "text, expected",
    [
        ("zorblat_sum", "zorblat_sum"),
        ("/v1/plinket_rate", "plinket_rate"),
        ("capability 'zorblat_sum' as asked", "zorblat_sum"),
        ("/v1/xzorblat_sum", "-"),
        ("zorblat_sum_extra", "-"),
        ("", "-"),
    ],
)
def test_named_capability_returns_only_a_blueprint_id(text, expected):
    assert grader._named_capability(text, ["zorblat_sum", "plinket_rate"]) == expected
