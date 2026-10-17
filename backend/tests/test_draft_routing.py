"""Draft routing regression tests.

Contract:
- LLM drafting must run BEFORE the golden-steward shortcut when enabled.
- The word "estate" alone must NOT preempt the LLM (vineyard estate, real
  estate, estate planning briefs should reach the architect).
- Golden steward remains the deterministic fallback for explicit steward
  intent when LLM drafting is disabled.
- Golden lettings DOES preempt the LLM. A keyed Floor drafted branded
  lettings briefs as a property-management GENERATE stub (live d4f6da2).
"""

from __future__ import annotations

import pytest

from app.factory import product_architect
from app.factory.blueprint import ProductBlueprint


@pytest.fixture
def fake_dual_registry(monkeypatch):
    ids = ["audit", "chat", "vector_search", "ocr"]
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: ids)
    return ids


@pytest.fixture
def vineyard_blueprint() -> ProductBlueprint:
    return ProductBlueprint.model_validate(
        {
            "schema_version": "product_blueprint.v1",
            "product_id": "vineyard_management",
            "product_name": "Vineyard Estate Ops",
            "vertical": "vineyard_management",
            "summary": "Vineyard management for a Tuscan estate.",
            "factory_scenario": "CREATE_PRODUCT",
            "capabilities": [
                {
                    "id": "fermentation_tanks",
                    "description": "Track fermentation lots",
                    "block_ids": [],
                    "strategy_hint": "GENERATE",
                }
            ],
            "ui_modules": ["command_center", "operational_chat"],
            "connectors": [],
            "edge_profile": "standard",
            "human_authority": True,
        }
    )


def test_vineyard_estate_brief_reaches_llm(
    monkeypatch, fake_dual_registry, vineyard_blueprint
):
    """A brief about a vineyard estate must not short-circuit into steward."""
    calls = []

    def _fake_draft_with_llm(brief, *, vertical_hint=None):
        calls.append((brief, vertical_hint))
        return vineyard_blueprint

    monkeypatch.setenv(product_architect.LLM_DRAFTING_ENV, "true")
    monkeypatch.setattr(product_architect, "_draft_with_llm", _fake_draft_with_llm)

    brief = (
        "Build me a vineyard management platform for a family winery: "
        "track fermentation tanks, barrel inventory across two cellars, "
        "harvest scheduling by sugar readings, and club member shipments. "
        "We operate a 40-hectare estate in Tuscany."
    )
    bp = product_architect.draft_blueprint_from_brief(brief)

    assert len(calls) == 1, "LLM drafter must be invoked before any deterministic shortcut"
    assert bp.product_id != "projectsix"
    assert bp.product_id == "vineyard_management"


