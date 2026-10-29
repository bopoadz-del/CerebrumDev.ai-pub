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


def _ask(*args, **kwargs):
    """answer_query, with any crash or a missing labeled_answer surfaced as an
    assertion failure (so a mutant shows as a failed assertion, not a crash)."""
    try:
        ans = answer_query(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise AssertionError(f"answer_query raised {type(exc).__name__}: {exc}") from None
    assert ans.labeled_answer is not None, "answer carries no labeled_answer"
    return ans


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
    ans = _ask("t-a", d["id"].replace("_", " "), "prose 0.99", engine=_Engine(text))
    assert ans.kind == KIND_KERNEL and ans.labeled_answer is not None
    [claim] = ans.labeled_answer.claims
    assert claim.layer == 1 and claim.human_label == "Certified"
    assert claim.object_id == f"{set_id}:{d['id']}_v{d['definition_version']}"
    assert claim.precedence is None
    assert ans.kernel_value == pytest.approx(float(safe_eval(d["expression"], vals)))
    assert ans.labeled_answer.to_dict()["claims"][0]["layer"] == 1


def test_improvised_answer_is_not_layer_1():
    ans = _ask("t-a", "zzqx nonkernel subject", "prose", engine=_Engine("nothing"))
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
    ans = _ask("t-a", d["id"].replace("_", " "), "p", engine=_Engine(text), overlays=[overlay])
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
    ans = _ask("t-a", "widget yield ratio", "p",
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
    ans = _ask("t-a", d["id"].replace("_", " "), "p", engine=_Engine(text),
                       overlays=[{"set_id": "tenant-rules", "definitions": [row]}])
    rec = ans.labeled_answer.claims[0].precedence
    assert rec is not None, "override claim carries no divergence record"
    assert rec.winner_result == pytest.approx(float(safe_eval(expr, vals)))
    assert rec.loser_result == pytest.approx(float(safe_eval(d["expression"], vals)))


# ---- repair 1 (G2 M9 / M9b / M17) ----
def _citing_engine(text, seed):
    src = f"ledger_{seed}.xlsx"
    eng = _Engine(text)
    orig = eng.retrieve
    eng.retrieve = lambda c, q: [_Hit(h.text, file=src, location={"sheet": "S", "row": seed})
                                 for h in orig(c, q)]
    return eng, [{"file": src, "location": {"sheet": "S", "row": seed}}]


def _path_case(path, seed):
    """(query, overlays, names) for each claim path: base, extension, override."""
    set_id, d = _base()
    query = d["id"].replace("_", " ")
    if path == "base":
        return query, (), free_names(d["expression"])
    if path == "override":
        overlay, expr = _override_overlay(set_id, d)
        return query, [overlay], free_names(expr)
    ext = {"set_id": "tenant-x", "definitions": [{
        "id": "widget_yield_ratio", "definition_version": 1, "expression": "widgets / batches",
        "provenance": {"kind": "client_submitted", "reference": "r"}}]}
    return "widget yield ratio", [ext], ["widgets", "batches"]


# repair: G2 M9 (base), M9b (override), M9c (extension) -- every claim path
@pytest.mark.parametrize("path", ["base", "extension", "override"])
@pytest.mark.parametrize("seed", [2, 5])
def test_kernel_labeled_answer_carries_the_retrieved_citations(path, seed):
    query, overlays, names = _path_case(path, seed)
    _, text = _corpus(names, seed)
    eng, expected = _citing_engine(text, seed)
    ans = _ask("t-a", query, "p", engine=eng, overlays=overlays)
    assert ans.kind == KIND_KERNEL and not ans.unknown
    expected_layer = 1 if path == "base" else 3
    assert [c.layer for c in ans.labeled_answer.claims] == [expected_layer]
    assert (ans.labeled_answer.claims[0].precedence is not None) == (path == "override")
    cites = [c.to_dict() for c in ans.labeled_answer.citations]
    assert cites == expected
    assert ans.labeled_answer.to_dict()["citations"] == cites == [c.to_dict() for c in ans.citations]


def _divisor_base():
    base = load_base_definitions()
    for d in base["definitions"]:
        expr = d.get("expression") or ""
        if "/" in expr and len(free_names(expr)) >= 2:
            divisor = free_names(expr.split("/", 1)[1].strip().strip("()"))[0]
            return base["set_id"], d, divisor
    raise AssertionError("no divisor base definition")


@pytest.mark.parametrize("seed", [2, 5])
def test_certified_divide_by_zero_is_not_invented_and_does_not_crash(seed):
    set_id, d, divisor = _divisor_base()
    names = free_names(d["expression"])
    expr = " + ".join(names)  # the tenant rule has no division
    # the tenant rule drops the base's divisor guard (it does not divide), so the
    # zero divisor reaches only the certified (base) computation
    row = dict(d, expression=expr, guards=[], overrides=f"{set_id}:{d['id']}_v{d['definition_version']}",
               reason="tenant rule", provenance={"kind": "client_submitted", "reference": "r"})
    vals = {n: (0.0 if n == divisor else float(seed * (i + 3))) for i, n in enumerate(names)}
    text = "\n".join(f"{n} = {v}" for n, v in vals.items())
    ans = _ask("t-a", d["id"].replace("_", " "), "p", engine=_Engine(text),
                       overlays=[{"set_id": "tenant-rules", "definitions": [row]}])
    assert not ans.unknown and ans.kernel_value == pytest.approx(sum(vals.values()))
    rec = ans.labeled_answer.claims[0].precedence
    assert rec is not None, "override claim carries no divergence record"
    assert rec.winner_result == pytest.approx(sum(vals.values()))
    assert not isinstance(rec.loser_result, (int, float))  # no invented certified number
