"""The writer receives the customer's per-capability requirements verbatim,
and the prompt states the source-of-record and requirements-coverage
contracts.

Live pilot 42481327502 graded NOT_CERTIFIED on
``contract_and_boq_source_of_record`` and ``claim_review_and_dispute_resolution``:
the writer only ever received one-line capability descriptions, never the
customer's per-capability asks (clause/BOQ/traceability), and the prompt had
no immutable-ingestion contract. The requirements now travel architect ->
blueprint -> plan -> intake -> brief, and the prompt states the contract.
"""

from __future__ import annotations

from app.factory.blueprint import CapabilitySpec, ProductBlueprint
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.intake_blueprint import intake_from_product_blueprint
from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt
from app.factory.product_architect import plan_blueprint


def _blueprint(requirements):
    """Two inputs per decision: a capability with no asks, and one with asks."""
    cap = CapabilitySpec(
        id="contract_and_boq_source_of_record",
        description="Contract and BOQ ingest",
        block_ids=[],
        strategy_hint="GENERATE",
        requirements=requirements,
    )
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="test-product",
        product_name="Test Product",
        vertical="construction",
        summary="test",
        capabilities=[cap],
    )


def test_capability_spec_keeps_requirements():
    assert _blueprint([]).capabilities[0].requirements == []
    assert _blueprint(["clause traceability"]).capabilities[0].requirements == [
        "clause traceability"
    ]


def test_planner_carries_requirements():
    assert plan_blueprint(_blueprint([])).capabilities[0].requirements == []
    assert plan_blueprint(_blueprint(["a", "b"])).capabilities[0].requirements == [
        "a",
        "b",
    ]


def test_intake_carries_requirements_and_full_source_text():
    bp = _blueprint(["keep these words"])
    intake = intake_from_product_blueprint(bp, brief="long user turn " + "x" * 500)
    cap = intake["capabilities"][0]
    assert cap["requirements"] == ["keep these words"]
    assert len(cap["source_text"]) > 400  # full turn text, no 400-char loss
    # Second input: no requirements -> empty list, still valid.
    intake2 = intake_from_product_blueprint(_blueprint([]), brief="short")
    assert intake2["capabilities"][0]["requirements"] == []


def test_brief_renders_requirements_verbatim():
    bp = _blueprint(["clause 8.2 traceable", "BOQ P1-P5"])
    text = compile_brief(bp, plan_blueprint(bp)).text
    assert "requirement: clause 8.2 traceable" in text
    assert "requirement: BOQ P1-P5" in text
    # Second input: no requirements -> no such line rendered.
    bp2 = _blueprint([])
    text2 = compile_brief(bp2, plan_blueprint(bp2)).text
    assert "requirement: clause 8.2 traceable" not in text2


def test_writer_prompt_states_source_of_record_and_coverage():
    text = render_writer_prompt(_blueprint([]), brief="build it")
    assert PROMPT_VERSION == "writer_worker_prompt.v16"
    assert "SOURCE OF RECORD" in text
    assert "REQUIREMENTS COVERAGE" in text
