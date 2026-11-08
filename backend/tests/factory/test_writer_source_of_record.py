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

import pytest

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


@pytest.mark.parametrize(
    "requirements,expected",
    [
        ([], []),
        (["clause traceability"], ["clause traceability"]),
    ],
)
def test_capability_spec_keeps_requirements(requirements, expected):
    assert _blueprint(requirements).capabilities[0].requirements == expected


@pytest.mark.parametrize(
    "requirements,expected",
    [
        ([], []),
        (["a", "b"], ["a", "b"]),
    ],
)
def test_planner_carries_requirements(requirements, expected):
    assert plan_blueprint(_blueprint(requirements)).capabilities[0].requirements == expected


@pytest.mark.parametrize(
    "requirements,brief,expected",
    [
        (["keep these words"], "long user turn " + "x" * 500, ["keep these words"]),
        ([], "short", []),
    ],
)
def test_intake_carries_requirements(requirements, brief, expected):
    intake = intake_from_product_blueprint(_blueprint(requirements), brief=brief)
    cap = intake["capabilities"][0]
    assert cap["requirements"] == expected


@pytest.mark.parametrize(
    "requirements",
    [
        ["clause 8.2 traceable", "BOQ P1-P5"],
        [],
    ],
)
def test_brief_renders_requirements_verbatim(requirements):
    bp = _blueprint(requirements)
    text = compile_brief(bp, plan_blueprint(bp)).text
    for req in requirements:
        assert f"requirement: {req}" in text
    if not requirements:
        assert "requirement: clause 8.2 traceable" not in text


@pytest.mark.parametrize(
    "requirements",
    [
        [],
        ["clause 8.2 traceable"],
    ],
)
def test_writer_prompt_states_source_of_record_and_coverage(requirements):
    text = render_writer_prompt(_blueprint(requirements), brief="build it")
    assert PROMPT_VERSION == "writer_worker_prompt.v16"
    assert "SOURCE OF RECORD" in text
    assert "REQUIREMENTS COVERAGE" in text
