"""F-3 (H9) completion: the RENDERED product pack is keyed on the product's
vertical -- never a hardwired construction kit / prompt / kernel pack.

``domain_pack_for`` is the pack the build renders into the product's
``docs/domain_pack.json`` (the prompt/kernel pack WRITER ships), and
``render_main`` is the rendered product entrypoint. Both derive their content
from the blueprint's vertical. This test proves the PACK LOADER keys on the
product's domain:

* a NO_VERTICAL (neutral) product renders a pack with NO construction content;
* two different verticals render two DIFFERENT packs (input-derived): a
  construction product renders the construction pack, a logistics product does
  NOT -- and vice versa;
* a pack loader that ignores the product domain (a fixed construction pack)
  must redden the neutral test (see the constant-pack mutant note below).

The derivation is a pure function of the blueprint (no LLM, no file copy), so
the test renders the pack/entrypoint in-process -- it does not drive the full
file-tree generation (whose Store-clone step hits a pre-existing Windows
long-path failure, unrelated to H9).
"""

from __future__ import annotations

import json

from app.factory.blueprint import FactoryScenario, ProductBlueprint
from app.factory.build.deploy import render_main
from app.factory.build.domain_pack import domain_pack_for
from app.factory.store_kits import NO_VERTICAL


def _blueprint(vertical: str) -> ProductBlueprint:
    """A minimal blueprint carrying *vertical*, shaped like the architect's."""
    return ProductBlueprint.model_validate(
        {
            "schema_version": "product_blueprint.v1",
            "product_id": vertical.replace("_", "-"),
            "product_name": vertical.replace("_", " ").title() + " Platform",
            "vertical": vertical,
            "summary": f"A platform for {vertical} operations",
            "factory_scenario": FactoryScenario.CREATE_PRODUCT.value,
            "capabilities": [
                {
                    "id": f"{vertical.replace('-', '_')}_core",
                    "description": "Core workflows and data model",
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


def _rendered_text(vertical: str):
    """Everything the build renders for *vertical*: the domain/prompt/kernel
    pack JSON plus the product entrypoint source. Returns (text, pack)."""
    bp = _blueprint(vertical)
    pack = domain_pack_for(bp, None)
    main_src = render_main(bp.product_name, vertical)
    return json.dumps(pack).lower() + "\n" + main_src.lower(), pack


def test_neutral_product_renders_no_construction_pack():
    # A NO_VERTICAL product renders a neutral pack + entrypoint with NO
    # construction kit / prompt / kernel content anywhere.
    text, pack = _rendered_text(NO_VERTICAL)
    assert "construction" not in text
    # The pack is keyed on the product's (neutral) domain, not a baked vertical.
    assert pack["domain_id"] == NO_VERTICAL
    assert pack["header"]["domain"] == NO_VERTICAL


def test_two_verticals_render_two_different_packs():
    # Two different verticals -> two different rendered packs (input-derived):
    # the construction product renders the construction pack; the logistics
    # product does NOT (and vice versa).
    con_text, con_pack = _rendered_text("construction")
    log_text, log_pack = _rendered_text("logistics")

    assert con_pack["domain_id"] == "construction"
    assert log_pack["domain_id"] == "logistics"
    assert con_pack["domain_id"] != log_pack["domain_id"]

    # Each rendered pack carries its OWN vertical and not the other's.
    assert "construction" in con_text
    assert "construction" not in log_text
    assert "logistics" in log_text
    assert "logistics" not in con_text

    # And the neutral product carries neither.
    neutral_text, _ = _rendered_text(NO_VERTICAL)
    assert "construction" not in neutral_text
    assert "logistics" not in neutral_text
