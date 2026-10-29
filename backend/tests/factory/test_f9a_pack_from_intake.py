"""F-9a: the Domain Pack is built from the intake, the brief turns and the
kernel's definition addresses -- never from Factory-invented literals -- and
assert_pack can fail (an empty intake fails)."""

from __future__ import annotations

import json

import pytest

from app.cerebrum_product_kernel.formulas import definition_index
from app.factory.blueprint import CapabilitySpec, ProductBlueprint
from app.factory.build.brief_compiler import synthesize_domain_pack
from app.factory.build.domain_pack import DomainPackError, assert_pack, domain_pack_for
from app.factory.build.intake_blueprint import (
    IntakeBlueprintError,
    intake_from_product_blueprint,
    validate_intake,
)


def _bp(name, vertical, summary, caps, connectors=()):
    return ProductBlueprint(
        schema_version="product_blueprint.v1", product_id=name.lower().replace(" ", "_"),
        product_name=name, vertical=vertical, summary=summary,
        capabilities=[CapabilitySpec(id=c, description=f"{c} words") for c in caps],
        connectors=list(connectors),
    )


A = _bp("Kiln Log", "pottery_studio", "Track kiln firings.", ["firing_log", "glaze_batch"])
B = _bp("Dock Board", "harbour_ops", "Berth allocation for tugs.", ["berth_plan"], ["ais_feed"])

INVENTED = ("operators", "operator", "admin", "local sqlite", "vendored store blocks",
            "none claimed", "operations platform")


def _values(pack):
    return json.dumps({k: v.get("value") for k, v in pack["fields"].items()}).lower()


@pytest.mark.parametrize("bp", [A, B], ids=["A", "B"])
def test_pack_carries_no_factory_invented_values(bp):
    blob = _values(domain_pack_for(bp))
    for word in INVENTED:
        assert f'"{word}' not in blob, word


def test_two_intakes_give_two_packs():
    pa, pb = domain_pack_for(A), domain_pack_for(B)
    assert pa["fields"]["required_product_modules"]["value"] == ["firing_log", "glaze_batch"]
    assert pb["fields"]["required_product_modules"]["value"] == ["berth_plan"]
    assert pb["fields"]["required_connectors"]["value"] == ["ais_feed"]
    assert pa["fields"]["required_connectors"].get("absent") is True
    assert pa["header"]["mission"] == "Track kiln firings."
    assert pb["header"]["mission"] == "Berth allocation for tugs."


def test_calculations_are_the_kernel_definition_addresses():
    want = [r["address"] for r in definition_index()]
    assert want
    vals = synthesize_domain_pack(A, None)["authoritative_calculations"]
    assert vals == want


def test_unsupplied_fields_are_declared_absent_not_filled():
    pack = domain_pack_for(A)
    for name in ("primary_users", "required_roles", "data_sources"):
        item = pack["fields"][name]
        assert item.get("absent") is True, name
        assert item["value"] == [], name
    assert_pack(pack)


def test_empty_intake_fails_assert_pack():
    # ProductBlueprint refuses an empty roster itself; the pack builder takes
    # any blueprint-shaped object, so an empty intake is modelled directly.
    from types import SimpleNamespace
    empty = SimpleNamespace(product_id="x", product_name="X", vertical="x_vertical",
                            summary="", capabilities=[], connectors=[])
    with pytest.raises(DomainPackError, match="intake"):
        assert_pack(domain_pack_for(empty))


def test_silently_blank_field_fails_assert_pack():
    pack = domain_pack_for(A)
    pack["fields"]["required_roles"].pop("absent")
    with pytest.raises(DomainPackError, match="required_roles"):
        assert_pack(pack)


TURNS_A = [{"turn": 1, "role": "user", "text": "kiln log please"},
           {"turn": 2, "role": "user", "text": "glazers and kiln techs use it"}]
TURNS_B = [{"turn": 1, "role": "user", "text": "glazers only, from turn one"}]


@pytest.mark.parametrize("turns,expect", [(TURNS_A, 2), (TURNS_B, 1)], ids=["A", "B"])
def test_provenance_is_the_turn_that_says_it(turns, expect):
    pack = {"primary_users": ["glazers"]}
    intake = intake_from_product_blueprint(A, chat_turns=turns, domain_pack=pack)
    assert intake["users"]["source_turn"] == expect
    assert "glazers" in intake["users"]["source_text"]


def test_value_no_turn_says_is_not_stamped_on_a_turn():
    intake = intake_from_product_blueprint(
        A, chat_turns=TURNS_A, domain_pack={"required_roles": ["harbourmaster"]})
    assert intake["roles"]["source_turn"] is None
    assert intake["roles"]["source"] == "architect"
    assert intake["users"]["absent"] is True and intake["users"]["value"] == []


def test_fabricated_turn_provenance_is_refused():
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"primary_users": ["glazers"]})
    intake["users"] = dict(intake["users"], value=["harbourmaster"])
    with pytest.raises(IntakeBlueprintError, match="users"):
        validate_intake(intake, chat_turns=TURNS_A)
