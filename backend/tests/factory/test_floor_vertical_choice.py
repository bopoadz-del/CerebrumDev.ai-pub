"""The vertical is the USER's choice on the Floor -- never inferred.

Owner order (2026-10-05): the user picks or types the vertical; the Factory
never infers it from the brief's prose or from the blocks a draft binds. No
choice -> vertical "product", no domain kit, and the build still completes.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.golden_match import Golden, eligible_for
from app.factory.product_architect import (
    _blueprint_from_llm_payload,
    draft_blueprint_from_brief,
)
from app.factory.store_kits import (
    NO_VERTICAL,
    SERVED,
    UNSUPPORTED,
    chosen_vertical,
    declared_verticals,
    domain_kits,
    resolve_vertical,
)

PROSE_NAMING_A_VERTICAL = (
    "We run zorblat yards. Build our zorblat yard platform for the zorblat "
    "industry: intake, yard moves and invoicing."
)


def _store(tmp_path: Path) -> Path:
    """A Store root with two invented domain kits."""
    kits = tmp_path / "store" / "block_store" / "kits"
    for kit_id, manifest in {
        "zorblat": {"id": "zorblat", "serves_verticals": ["zorblat_yards"],
                    "build_ready": True, "blocks": []},
        "quux": {"id": "quux", "serves_verticals": ["quux_depots"],
                 "excludes_verticals": ["quux_kennels"], "build_ready": False,
                 "blocks": []},
    }.items():
        (kits / kit_id).mkdir(parents=True)
        (kits / kit_id / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path / "store"


def test_no_choice_yields_product():
    assert chosen_vertical(None) == NO_VERTICAL == "product"
    assert chosen_vertical("") == "product"
    assert chosen_vertical("  Zorblat Yards ") == "zorblat_yards"


def test_prose_that_names_a_vertical_still_yields_product():
    bp = draft_blueprint_from_brief(PROSE_NAMING_A_VERTICAL, vertical_hint=None, use_llm=False)
    assert bp.vertical == "product"
    assert bp.drafting_mode != "golden"


def test_the_models_vertical_is_ignored_without_a_user_choice():
    payload = {
        "product_name": "Zorblat Ops",
        "vertical": "zorblat_yards",  # the model's reading of the prose
        "capabilities": [{"id": "yard_moves", "description": "x", "block_ids": []}],
    }
    assert _blueprint_from_llm_payload(payload, "brief", None, []).vertical == "product"
    assert _blueprint_from_llm_payload(payload, "brief", "quux depots", []).vertical == "quux_depots"


def test_with_a_choice_the_draft_carries_exactly_that_vertical():
    bp = draft_blueprint_from_brief("anything at all", vertical_hint="Zorblat Yards", use_llm=False)
    assert bp.vertical == "zorblat_yards"


def test_a_golden_is_eligible_only_for_the_vertical_the_user_chose():
    a = Golden(path=Path("a.yaml"), name="A", structure=frozenset({"x"}), serves=frozenset({"zorblat_yards"}))
    b = Golden(path=Path("b.yaml"), name="B", structure=frozenset({"x"}), serves=frozenset({"quux_depots"}))
    assert eligible_for("zorblat yards", [a, b]) == [a]
    assert eligible_for("product", [a, b]) == []
    assert eligible_for(None, [a, b]) == []


def test_the_kit_resolves_from_the_users_choice_only(tmp_path):
    root = _store(tmp_path)
    kits = domain_kits(root)
    assert resolve_vertical(chosen_vertical("zorblat_yards"), kits)[:2] == (SERVED, "zorblat")
    status, kit, reason = resolve_vertical(chosen_vertical("quux kennels"), kits)
    assert (status, kit) == (UNSUPPORTED, "quux") and "excluded" in reason
    # No choice: nothing serves "product", so no domain kit is resolved.
    assert resolve_vertical(chosen_vertical(None), kits)[1] is None


def test_the_floor_picker_offers_what_the_kits_declare(tmp_path):
    rows = declared_verticals(_store(tmp_path))
    assert [(r["vertical"], r["kit"], r["build_ready"]) for r in rows] == [
        ("quux_depots", "quux", False),
        ("zorblat_yards", "zorblat", True),
    ]


def test_a_build_with_no_choice_completes_with_no_domain_kit(tmp_path, stub_coder):
    """The owner's proof: no hint -> vertical "product", the build completes,
    and no domain kit is resolved -- the universal path."""
    from app.factory.build.reasoning_socket import kit_for_vertical
    from app.factory.build.runner import RoleRunner

    bp = draft_blueprint_from_brief(PROSE_NAMING_A_VERTICAL, vertical_hint=None, use_llm=False)
    assert bp.vertical == "product"
    outcome = RoleRunner(bp, tmp_path / "build").run()
    assert outcome.ok, outcome.detail
    assert kit_for_vertical(bp, store_root=_store(tmp_path)) is None
