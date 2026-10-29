"""K-F3: a kernel answer carries per-claim layer labels.

* base-tier definition -> one Claim, layer 1 ("Certified");
* improvised answer -> its claim is NOT layer 1 (documents layer);
* a tenant overlay overriding a base id -> layer 3 ("Your formula") with a
  formula_shadow DivergenceRecord carrying BOTH computed values;
* a tenant extension (new id) -> layer 3, no divergence.
Values are computed from grounded figures in the stub corpus; two corpora
give two different results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

from app.cerebrum_product_kernel.answer import KIND_IMPROVISED, KIND_KERNEL, answer_query
from app.cerebrum_product_kernel.claim_labels import Citation, Claim, LabeledAnswer
from app.cerebrum_product_kernel.formulas import load_base_definitions
from app.cerebrum_product_kernel.formulas.evaluator import free_names, safe_eval
from app.cerebrum_product_kernel.precedence import RULE_FORMULA_SHADOW


@dataclass
class _Hit:
    text: str
    file: str = "figures.txt"
    location: Dict[str, Any] = field(default_factory=dict)


class _Engine:
    def __init__(self, text: str):
        self.text = text

    def retrieve(self, collection, query):
        # Only figures whose name the grounding query asks for are retrieved,
        # so an input left out of the query is genuinely not grounded.
        words = set(query.split())
        lines = [ln for ln in self.text.splitlines() if ln.split(" =")[0] in words]
        return [_Hit("\n".join(lines))]

    def labeled_answer(self, collection, query, answer_text):
        return LabeledAnswer(
            claims=[Claim(text=answer_text, layer=2, object_id="doc:figures.txt")],
            citations=[Citation(file="figures.txt")],
        )


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "s"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "f"))


def _base():
    base = load_base_definitions()
    # first base definition with a 2+-input expression, chosen from data
    for d in base["definitions"]:
        if d.get("expression") and len(free_names(d["expression"])) >= 2:
            return base["set_id"], d
    raise AssertionError("no multi-input base definition")


def _corpus(names, seed):
    vals = {n: float(seed * (i + 3) + 7) for i, n in enumerate(names)}
    return vals, "\n".join(f"{n} = {v}" for n, v in vals.items())


@pytest.mark.parametrize("seed", [2, 5])
def test_base_kernel_answer_is_layer_1(seed):
    set_id, d = _base()
    vals, text = _corpus(free_names(d["expression"]), seed)
    ans = answer_query("t-a", d["id"].replace("_", " "), "prose 0.99", engine=_Engine(text))
    assert ans.kind == KIND_KERNEL and ans.labeled_answer is not None
    [claim] = ans.labeled_answer.claims
    assert claim.layer == 1 and claim.human_label == "Certified"
    assert claim.object_id == f"{set_id}:{d['id']}_v{d['definition_version']}"
    assert claim.precedence is None
    assert ans.kernel_value == pytest.approx(float(safe_eval(d["expression"], vals)))
    assert ans.labeled_answer.to_dict()["claims"][0]["layer"] == 1


def test_improvised_answer_is_not_layer_1():
    ans = answer_query("t-a", "zzqx nonkernel subject", "prose", engine=_Engine("nothing"))
    assert ans.kind == KIND_IMPROVISED
    assert [c.layer for c in ans.labeled_answer.claims] == [2]


def _override_overlay(set_id, d, tenant_set="tenant-rules"):
    names = free_names(d["expression"])
    expr = f"({d['expression']}) * 2 + {names[0]}"  # a genuinely different formula
    row = dict(d, expression=expr, overrides=f"{set_id}:{d['id']}_v{d['definition_version']}",
               reason="tenant rule", provenance={"kind": "client_submitted", "reference": "r"})
    return {"set_id": tenant_set, "definitions": [row]}, expr


@pytest.mark.parametrize("seed", [2, 5])
def test_override_is_layer_3_with_shadow_divergence(seed):
    set_id, d = _base()
    overlay, expr = _override_overlay(set_id, d)
    vals, text = _corpus(free_names(expr), seed)
    ans = answer_query("t-a", d["id"].replace("_", " "), "p", engine=_Engine(text), overlays=[overlay])
    [claim] = ans.labeled_answer.claims
    assert claim.layer == 3 and claim.human_label == "Your formula"
    rec = claim.precedence
    assert rec is not None and rec.rule_id == RULE_FORMULA_SHADOW
    assert (rec.winner_layer, rec.loser_layer) == (3, 1)
    assert rec.winner_result == pytest.approx(float(safe_eval(expr, vals)))
    assert rec.loser_result == pytest.approx(float(safe_eval(d["expression"], vals)))
    assert rec.winner_result != rec.loser_result
    assert claim.to_dict()["precedence"]["loser_result"] == rec.loser_result


def test_extension_is_layer_3_without_divergence():
    ext = {"set_id": "tenant-x", "definitions": [{
        "id": "widget_yield_ratio", "definition_version": 1, "expression": "widgets / batches",
        "provenance": {"kind": "client_submitted", "reference": "r"}}]}
    ans = answer_query("t-a", "widget yield ratio", "p",
                       engine=_Engine("widgets = 40\nbatches = 8"), overlays=[ext])
    [claim] = ans.labeled_answer.claims
    assert ans.kernel_value == 5
    assert claim.layer == 3 and claim.precedence is None
    assert claim.object_id == "tenant-x:widget_yield_ratio_v1"


def test_certified_value_is_grounded_even_when_the_override_drops_its_inputs():
    set_id, d = _base()
    names = free_names(d["expression"])
    expr = f"{names[0]} * 2 + tenant_adjustment"  # drops names[1:]
    row = dict(d, expression=expr, overrides=f"{set_id}:{d['id']}_v{d['definition_version']}",
               reason="tenant rule", provenance={"kind": "client_submitted", "reference": "r"})
    vals, text = _corpus(names + ["tenant_adjustment"], 3)
    ans = answer_query("t-a", d["id"].replace("_", " "), "p", engine=_Engine(text),
                       overlays=[{"set_id": "tenant-rules", "definitions": [row]}])
    rec = ans.labeled_answer.claims[0].precedence
    assert rec.winner_result == pytest.approx(float(safe_eval(expr, vals)))
    assert rec.loser_result == pytest.approx(float(safe_eval(d["expression"], vals)))
