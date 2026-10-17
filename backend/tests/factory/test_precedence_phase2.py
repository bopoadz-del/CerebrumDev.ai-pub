"""Phase 2 acceptance: authority precedence as data, logged, never
model-decided. T2.1-T2.5 against the real kernel precedence engine.
"""

from __future__ import annotations

import json

import pytest

from app.cerebrum_product_kernel.precedence import (
    LAYER_LABELS,
    PRECEDENCE_VERSION,
    RULE_FORMULA_SHADOW,
    LayerObject,
    PrecedenceError,
    load_ladder,
    resolve_formula_by_id,
    resolve_precedence,
)


def _certified_formula(value):
    return LayerObject(
        object_id="margin_v1",
        layer=1,
        tenant_id=None,
        evaluate=lambda: value,
    )


def _taught_formula(value, tenant="tenant_a"):
    return LayerObject(
        object_id="margin_v1",
        layer=3,
        tenant_id=tenant,
        evaluate=lambda: value,
    )


def _certified_text(excerpt):
    return LayerObject(object_id="policy_x", layer=1, excerpt=excerpt)


def _client_doc(excerpt, tenant="tenant_a"):
    return LayerObject(
        object_id="policy_x", layer=2, tenant_id=tenant, excerpt=excerpt
    )


# -- T2.1: taught formula outranks certified, with the divergence log --------


def test_taught_formula_outranks_certified():
    taught = _taught_formula(42.0)
    certified = _certified_formula(40.0)
    verdict = resolve_formula_by_id("margin_v1", [certified, taught])

    assert verdict.winner.layer == 3
    assert verdict.winner.result is None  # value comes from evaluate()
    shadow = verdict.records[0]
    assert shadow.rule_id == RULE_FORMULA_SHADOW
    assert shadow.winner_layer == 3
    assert shadow.loser_layer == 1
    assert shadow.winner_result == 42.0
    assert shadow.loser_result == 40.0


# -- T2.2: client document outranks certified text ----------------------------


def test_client_document_outranks_certified_text():
    certified = _certified_text("certified excerpt")
    client = _client_doc("client excerpt")
    verdict = resolve_precedence([certified, client])

    assert verdict.winner.layer == 2
    assert verdict.winner.excerpt == "client excerpt"
    record = verdict.records[0]
    assert record.winner_layer == 2
    assert record.loser_layer == 1
    assert record.loser_result == "certified excerpt"


# -- T2.3: precedence is not model-decided ------------------------------------


def test_precedence_is_not_model_decided(monkeypatch):
    """Even with every LLM leg patched to 'prefer' the L1 value, the emitted
    winner is L3 — the resolver has no model path to consult."""
    from app.core import chain_generator

    tried = []

    async def prefer_l1(*args, **kwargs):
        tried.append(args)
        return {"message": "use the certified 40.0", "chain": [], "rules": []}

    monkeypatch.setattr(chain_generator, "_call_llm", prefer_l1)

    taught = _taught_formula(42.0)
    certified = _certified_formula(40.0)
    verdict = resolve_formula_by_id("margin_v1", [certified, taught])

    assert verdict.winner.layer == 3
    assert verdict.records[0].winner_result == 42.0
    assert tried == [], "resolution must never reach a model"


# -- T2.4: every answer carries the precedence log ----------------------------


def test_every_verdict_carries_precedence_log():
    verdict = resolve_formula_by_id(
        "margin_v1", [_certified_formula(40.0), _taught_formula(42.0)]
    )
    payload = verdict.to_dict()
    assert payload["precedence_version"] == PRECEDENCE_VERSION
    assert payload["winner_layer"] == 3
    assert payload["divergences"], "an answer without a precedence log is invalid"


def test_verdict_refuses_without_objects():
    with pytest.raises(PrecedenceError, match="precedence_no_objects"):
        resolve_precedence([])


# -- T2.5: divergence records BOTH results ------------------------------------


def test_divergence_recorded_with_both_results():
    certified = _certified_formula(40.0)
    taught = _taught_formula(42.0)
    verdict = resolve_formula_by_id("margin_v1", [certified, taught])

    record = verdict.records[0]
    assert record.winner_result == 42.0
    assert record.loser_result == 40.0
    assert record.winner_result != record.loser_result
    assert record.to_dict()["winner_result"] == 42.0


# -- layer typing: certified tenant-agnostic, 2-4 tenant-scoped ---------------


def test_layer_typing_refusals():
    with pytest.raises(PrecedenceError, match="certified_layer_tenant_scoped"):
        LayerObject(object_id="x", layer=1, tenant_id="tenant_a")
    with pytest.raises(PrecedenceError, match="tenant_layer_unscoped"):
        LayerObject(object_id="x", layer=2)
    with pytest.raises(PrecedenceError, match="unknown_layer"):
        LayerObject(object_id="x", layer=5)


def test_ladder_is_versioned_data_not_prompt_text():
    ladder = load_ladder()
    assert ladder == {1: 1, 2: 2, 3: 3, 4: 4}
    assert set(LAYER_LABELS) == set(ladder)


# -- P2: inverting the ladder flips the winner (the suite must go RED) --------


def test_inverted_ladder_flips_the_winner(tmp_path):
    """P2 mutation: rank 1 > 4 in the DATA and the taught formula loses.

    This is the control experiment run inside the suite; the standalone
    probe asserts the same inversion, and a gate that survived inversion
    would be a gate reading prompts, not data.
    """
    inverted = tmp_path / "inverted_ladder.json"
    inverted.write_text(
        json.dumps(
            {
                "schema": PRECEDENCE_VERSION,
                "rank": [
                    {"layer": 1, "rank": 4, "label": "certified"},
                    {"layer": 2, "rank": 3, "label": "documents"},
                    {"layer": 3, "rank": 2, "label": "formulas"},
                    {"layer": 4, "rank": 1, "label": "procedures"},
                ],
            }
        ),
        encoding="utf-8",
    )
    ladder = load_ladder(inverted)
    verdict = resolve_formula_by_id(
        "margin_v1", [_certified_formula(40.0), _taught_formula(42.0)], ladder=ladder
    )
    assert verdict.winner.layer == 1, "the ladder is baked in, not data"
