"""Phase 3 acceptance: per-claim layer labels, the citation-provenance port,
and the export round-trip. T3.1-T3.3 + P3 against the real kernel modules.
"""

from __future__ import annotations

import pytest

from app.cerebrum_product_kernel.citation_provenance import (
    CITABLE_CLASSES,
    KIND_RETRIEVAL,
    KIND_TOOL_RUN,
    KIND_USER,
    EvidenceRecord,
    build_evidence,
)
from app.cerebrum_product_kernel.claim_labels import (
    Claim,
    ClaimLabelError,
    LabeledAnswer,
    UNLABELED_CLAIM,
    enforce_claim_labels,
)
from app.cerebrum_product_kernel.precedence import (
    LayerObject,
    resolve_formula_by_id,
)


def _l1_claim():
    return Claim(text="Certified margin is 40.", layer=1, object_id="margin_v1")


def _l3_claim():
    taught = LayerObject(
        object_id="margin_v1", layer=3, tenant_id="tenant_a", evaluate=lambda: 42.0
    )
    certified = LayerObject(object_id="margin_v1", layer=1, evaluate=lambda: 40.0)
    verdict = resolve_formula_by_id("margin_v1", [certified, taught])
    return Claim(
        text="Your taught margin is 42.",
        layer=3,
        object_id="margin_v1",
        precedence=verdict.records[0],
    )


# -- T3.1: every claim carries a layer; unlabeled fails closed ----------------


def test_unlabeled_claim_fails_closed():
    with pytest.raises(ClaimLabelError) as exc:
        Claim(text="margin", layer=5, object_id="x")
    assert UNLABELED_CLAIM in str(exc.value)


def test_enforce_refuses_unlabeled_claims():
    unlabeled = Claim(text="fine claim", layer=2, object_id="x")
    unlabeled = Claim.__new__(Claim)  # bypass __post_init__ to strip the label
    with pytest.raises(ClaimLabelError) as exc:
        enforce_claim_labels([unlabeled])
    assert UNLABELED_CLAIM in str(exc.value)


# -- T3.2: the export record round-trips labels + precedence ------------------


def test_export_record_preserves_claim_labels_and_precedence():
    answer = LabeledAnswer(claims=[_l1_claim(), _l3_claim()])
    payload = answer.to_dict()

    assert payload["claims"][0]["layer"] == 1
    assert payload["claims"][0]["label"] == "Certified"
    assert payload["claims"][1]["layer"] == 3
    assert payload["claims"][1]["label"] == "Your formula"

    round_tripped = LabeledAnswer.from_dict(payload)
    assert round_tripped == answer
    assert round_tripped.claims[1].precedence is not None
    assert round_tripped.claims[1].precedence.winner_result == 42.0
    assert round_tripped.claims[1].precedence.loser_result == 40.0


# -- T3.3: a blended answer carries distinct per-claim labels ----------------


def test_blended_answer_has_distinct_claim_labels():
    answer = LabeledAnswer(claims=[_l1_claim(), _l3_claim()])
    assert {c.layer for c in answer.claims} == {1, 3}
    assert {c.human_label for c in answer.claims} == {
        "Certified",
        "Your formula",
    }
    assert len(answer.claims[1].precedence.to_dict()) == 7
    enforce_claim_labels(list(answer.claims))


# -- the port: evidence semantics preserved ----------------------------------


def test_evidence_record_citable_classes():
    retrieval = EvidenceRecord(
        kind=KIND_RETRIEVAL, text="chunk", source_class="project_corpus"
    )
    assert retrieval.reads_corpus is True
    assert retrieval.citable is True

    template = EvidenceRecord(
        kind=KIND_TOOL_RUN, tool="scheduler", inputs=""
    )
    assert template.reads_corpus is False
    assert template.citable is False

    user = EvidenceRecord(kind=KIND_USER, text="use DD-2022-175")
    assert user.citable is True


def test_build_evidence_maps_claims_to_records():
    evidence = build_evidence(
        rag_sys_msg={
            "chunks": [
                {"text": "certified margin text", "source_name": "base.json"},
            ]
        },
        messages=[
            {"role": "user", "content": "what is my margin"},
            {"role": "tool", "tool": "formula_executor", "content": "42.0"},
        ],
    )
    assert evidence.any_corpus_read() is True
    assert len(evidence.records) == 3
    assert "base.json" in evidence.source_names()
    assert "formula_executor" in evidence.tool_names()
    assert set(CITABLE_CLASSES) == {"project_corpus", "master_corpus"}


# -- P3: stripping the layer must fail the emission ---------------------------


def test_stripped_layer_fails_emit():
    """P3 mutation: a claim whose layer is stripped cannot be emitted."""
    claim = _l1_claim()
    stripped = Claim.__new__(Claim)  # bypass validation = the mutation
    answer = LabeledAnswer(claims=[stripped])
    with pytest.raises(ClaimLabelError, match=UNLABELED_CLAIM):
        answer.to_dict()
    enforce_claim_labels([claim])  # the labeled one still passes
