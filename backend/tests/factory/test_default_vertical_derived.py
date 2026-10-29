"""F-3 (H9): the vertical is DERIVED from the input, never a hardwired default.

There is no baked-in ``construction`` vertical anywhere in the generic product
code. A fresh session carries the NEUTRAL sentinel (``NO_VERTICAL``); a product
draft derives its vertical from the structured input (the Floor's
``vertical_hint``), and a draft that names no vertical keeps the sentinel --
downstream (``session_domain_from_blueprint`` -> ``SessionConfig.domain``) still
works generically on the sentinel.

The derivation here is a pure function of the INPUT (``vertical_hint`` ->
``chosen_vertical``), not an LLM call, so these tests need no ``llm_client``
stub and run fully offline (``use_llm=False``). Forcing the vertical to a
constant (the mutant below) must redden the two-input test.
"""

from __future__ import annotations

from app.factory.product_architect import (
    draft_blueprint_from_brief,
    session_domain_from_blueprint,
)
from app.factory.store_kits import NO_VERTICAL
from app.models.session import SessionConfig


def _derive(brief: str, vertical_hint):
    """Two different inputs -> two different derived verticals, offline."""
    bp = draft_blueprint_from_brief(
        brief, vertical_hint=vertical_hint, use_goldens=False, use_llm=False
    )
    return bp.vertical


def test_two_inputs_yield_two_derived_verticals():
    # Two different briefs naming two different verticals (the structured
    # ``vertical_hint`` each brief's intake carries) -> two DIFFERENT derived
    # verticals. This is input-derived, not a constant.
    v1 = _derive("build a platform for a logistics carrier", "logistics")
    v2 = _derive("build a platform for a hospitality group", "hospitality")

    assert v1 == "logistics"
    assert v2 == "hospitality"
    assert v1 != v2
    # And neither is the old hardwired default.
    assert "construction" not in {v1, v2}


def test_no_vertical_named_yields_neutral_sentinel():
    # A brief that names NO vertical -> the neutral sentinel, never
    # "construction".
    bp = draft_blueprint_from_brief(
        "build me a platform", vertical_hint=None, use_goldens=False, use_llm=False
    )
    assert bp.vertical == NO_VERTICAL
    assert bp.vertical != "construction"

    # Downstream still works generically on the sentinel: the session domain is
    # derived from the draft, and a fresh session config carries the sentinel
    # too -- no baked-in customer vertical anywhere.
    assert session_domain_from_blueprint(bp) == NO_VERTICAL
    assert session_domain_from_blueprint(None) == NO_VERTICAL
    assert session_domain_from_blueprint(None) != "construction"
    assert SessionConfig().domain == NO_VERTICAL
    assert SessionConfig().domain != "construction"


def test_session_domain_is_input_derived_two_inputs():
    # session_domain_from_blueprint is itself input-derived: two blueprints
    # carrying two verticals -> two different session domains.
    d1 = session_domain_from_blueprint({"vertical": "logistics"})
    d2 = session_domain_from_blueprint({"vertical": "hospitality"})
    assert d1 == "logistics"
    assert d2 == "hospitality"
    assert d1 != d2
