"""INTAKE CHAT → intake_blueprint.v1. Lettings golden reconstructs honestly."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.intake_blueprint import (
    SCHEMA_VERSION,
    intake_capability_ids,
    load_lettings_golden_chat,
    load_schema,
    reconstruct_intake_from_chat,
    render_plain_language,
    validate_intake,
)
from app.factory.product_architect import (
    draft_blueprint_from_brief,
)

ROOT = Path(__file__).resolve().parents[3]
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"
LIVE_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


def test_schema_is_the_collector_contract():
    schema = load_schema()
    assert schema["title"] == "intake_blueprint.v1"
    required = set(schema["required"])
    assert {
        "vertical",
        "capabilities",
        "roles",
        "users",
        "data_sources",
        "integrations",
        "constraints",
        "done_when",
    } <= required


def test_lettings_golden_chat_reconstructs_the_same_roster():
    """The intake reconstructed from a chat is the DRAFT's roster, sourced to
    the turn it came from -- a golden is never chosen by the chat's words."""

    chat = load_lettings_golden_chat()
    intake = reconstruct_intake_from_chat(chat["turns"], use_llm=False)
    validate_intake(intake)
    draft = draft_blueprint_from_brief(chat["turns"][0]["text"], use_llm=False)
    assert intake["schema_version"] == SCHEMA_VERSION
    assert set(intake_capability_ids(intake)) == {c.id for c in draft.capabilities}
    assert intake["vertical"]["value"] == draft.vertical
    assert intake["vertical"]["source_turn"] == 1
    for cap in intake["capabilities"]:
        assert cap["source_turn"] == 1
        assert cap["customer_words"]


def test_plain_language_names_done_when_and_approve():
    chat = load_lettings_golden_chat()
    # The vertical is the user's typed Floor choice, never read from the brief.
    intake = reconstruct_intake_from_chat(
        chat["turns"], use_llm=False, vertical_hint="residential_lettings"
    )
    prose = render_plain_language(intake)
    assert "Residential Lettings" in prose
    assert "Done when:" in prose
    assert "Approve the feature list" in prose
