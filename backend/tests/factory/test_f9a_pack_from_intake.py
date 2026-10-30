"""F-9a (split A of #95): the Domain Pack is built from the blueprint, the
capability plan and the kernel's definition addresses -- never from
Factory-invented literals -- and assert_pack can fail (an empty pack and an
empty intake each fail, with their own reason)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.cerebrum_product_kernel.formulas import definition_index
from app.factory.blueprint import CapabilitySpec, ProductBlueprint
from app.factory.build.brief_compiler import synthesize_domain_pack
from app.factory.build.domain_pack import DomainPackError, assert_pack, domain_pack_for

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


def _blank(**over):
    base = dict(product_id="x", product_name="", vertical="x_vertical",
                summary="", capabilities=[], connectors=[])
    base.update(over)
    return SimpleNamespace(**base)


class _PlanCap:
    def __init__(self, cid):
        self.capability_id = cid


class _Plan:
    def __init__(self, *cids):
        self.capabilities = tuple(_PlanCap(c) for c in cids)


class _BareBlueprint:
    """A stand-in that carries no capabilities of its own: the plan does."""

    def __init__(self, name, vertical, summary):
        self.product_name = name
        self.product_id = name.lower().replace(" ", "-")
        self.vertical = vertical
        self.summary = summary


class _MemWorkspace:
    def __init__(self):
        self.files = {}

    def write_text(self, rel, text):
        self.files[str(rel)] = text


@pytest.mark.parametrize("bp", [A, B], ids=["A", "B"])
def test_pack_carries_no_factory_invented_values(bp):
    blob = _values(domain_pack_for(bp))
    for word in INVENTED:
        assert f'"{word}' not in blob, word


@pytest.mark.parametrize("bp,modules,connectors,mission", [
    (A, ["firing_log", "glaze_batch"], None, "Track kiln firings."),
    (B, ["berth_plan"], ["ais_feed"], "Berth allocation for tugs."),
], ids=["A", "B"])
def test_two_intakes_give_two_packs(bp, modules, connectors, mission):
    pack = domain_pack_for(bp)
    assert pack["fields"]["required_product_modules"]["value"] == modules
    if connectors is None:
        assert pack["fields"]["required_connectors"].get("absent") is True
    else:
        assert pack["fields"]["required_connectors"]["value"] == connectors
    assert pack["header"]["mission"] == mission


@pytest.mark.parametrize("bp", [A, B], ids=["A", "B"])
def test_calculations_are_the_kernel_definition_addresses(bp):
    want = [r["address"] for r in definition_index()]
    assert want
    vals = synthesize_domain_pack(bp, None)["authoritative_calculations"]
    assert vals == want


@pytest.mark.parametrize("bp", [A, B], ids=["A", "B"])
def test_unsupplied_fields_are_declared_absent_not_filled(bp):
    pack = domain_pack_for(bp)
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


@pytest.mark.parametrize("field", ["domain_purpose", "required_product_modules"])
def test_each_intake_field_empty_on_its_own_fails_assert_pack(field):
    pack = domain_pack_for(A)
    assert_pack(pack)  # the full intake passes
    pack["fields"][field] = dict(pack["fields"][field], value=[], absent=True)
    with pytest.raises(DomainPackError, match=f"empty intake: no {field}$"):
        assert_pack(pack)


def test_blank_blueprint_gets_no_made_up_name_mission_or_workflows():
    syn = synthesize_domain_pack(_blank(), None)
    for key in ("platform_name", "mission", "domain_purpose",
                "core_business_workflows", "domain_acceptance_conditions",
                "required_connectors", "required_product_modules"):
        assert key not in syn, key
    assert all(v for v in syn.values())


@pytest.mark.parametrize("name,summary,cap", [
    ("Kiln Log", "Track kiln firings.", "firing_log"),
    ("Dock Board", "Berth allocation for tugs.", "berth_plan"),
])
def test_supplied_values_are_carried_verbatim(name, summary, cap):
    syn = synthesize_domain_pack(_bp(name, "pottery_studio", summary, [cap]), None)
    assert syn["platform_name"] == name
    assert syn["mission"] == summary
    assert syn["core_business_workflows"] == [f"one-record round-trip for {cap}"]
    assert "domain_acceptance_conditions" in syn


def test_pack_without_capabilities_has_no_acceptance_conditions():
    syn = synthesize_domain_pack(_blank(product_name="X", summary="s"), None)
    assert "domain_acceptance_conditions" not in syn
    assert "core_business_workflows" not in syn


@pytest.mark.parametrize("bp,modules", [(A, ["firing_log", "glaze_batch"]), (B, ["berth_plan"])],
                         ids=["A", "B"])
def test_delivery_brief_says_absent_for_an_absent_slot(bp, modules):
    from app.factory.build.domain_pack import as_delivery_domain_pack
    out = as_delivery_domain_pack(domain_pack_for(bp))
    assert out["required_roles"] == "absent: not supplied in the intake"
    assert out["required_product_modules"] == modules


@pytest.mark.parametrize(
    "bp,cids",
    [
        (_BareBlueprint("Kiln Log", "pottery_studio", "Track kiln firings."), ("firing_log", "glaze_batch")),
        (_BareBlueprint("Dock Board", "harbour_ops", "Berth allocation for tugs."), ("berth_plan",)),
    ],
)
def test_emit_domain_pack_reads_modules_from_the_plan(bp, cids):
    from app.factory.build.domain_pack import PACK_REL, emit_domain_pack

    ws = _MemWorkspace()
    try:
        out = emit_domain_pack(ws, blueprint=bp, plan=_Plan(*cids))
    except DomainPackError as exc:
        raise AssertionError(f"the plan's modules were not read: {exc}") from exc
    assert out["domain_id"] == bp.vertical
    text = ws.files[str(PACK_REL)]
    for cid in cids:
        assert cid in text
    pack = domain_pack_for(bp, _Plan(*cids))
    assert pack["fields"]["required_product_modules"]["value"] == list(cids)


def test_emit_domain_pack_without_a_plan_or_caps_is_an_empty_intake():
    from app.factory.build.domain_pack import emit_domain_pack

    bp = _BareBlueprint("Kiln Log", "pottery_studio", "Track kiln firings.")
    with pytest.raises(DomainPackError, match="required_product_modules"):
        emit_domain_pack(_MemWorkspace(), blueprint=bp)


@pytest.mark.parametrize(
    "bp,cids",
    [
        (_BareBlueprint("Kiln Log", "pottery_studio", "Track kiln firings."), ("firing_log", "glaze_batch")),
        (_BareBlueprint("Dock Board", "harbour_ops", "Berth allocation for tugs."), ("berth_plan",)),
    ],
)
def test_writer_builds_the_pack_from_its_plan(tmp_path, stub_coder, bp, cids):
    """The WRITER hands ctx.plan to emit_domain_pack: a blueprint whose roster
    lives only on the plan is NOT an empty intake, and the shipped pack lists
    exactly the plan's modules."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.domain_pack import PACK_REL
    from app.factory.build.roles import RoleContext, run_writer
    from app.factory.build.workspace import RoleWorkspace

    class _Cap:
        def __init__(self, cid):
            self.capability_id = cid
            self.block_ids = ()

    class _P:
        capabilities = tuple(_Cap(c) for c in cids)

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace(BuildRole.WRITER, tmp_path / "build"),
        blueprint=bp,
        plan=_P(),
        work_list=(),
        state={},
    )
    from app.factory.build.roles_models import RoleError

    try:
        result = run_writer(ctx)
    except RoleError as exc:
        raise AssertionError(f"writer did not build the pack from ctx.plan: {exc}") from exc
    assert result.ok
    shipped = json.loads((tmp_path / "build" / PACK_REL).read_text(encoding="utf-8"))
    assert shipped["domain_id"] == bp.vertical
    assert shipped["fields"]["required_product_modules"]["value"] == list(cids)
    assert shipped["fields"]["domain_purpose"]["value"] == bp.summary


@pytest.mark.parametrize("bp", [A, B], ids=["A", "B"])
def test_pack_invents_no_security_rule_or_demo_data(bp):
    # E11 / E12: nothing the build has supplies these slots.
    syn = synthesize_domain_pack(bp, None)
    assert "security_regulatory_rules" not in syn
    assert "demo_data_requirements" not in syn
    pack = domain_pack_for(bp)
    for name in ("security_regulatory_rules", "demo_data_requirements"):
        assert pack["fields"][name].get("absent") is True, name
        assert pack["fields"][name]["value"] == [], name


@pytest.mark.parametrize("flag,expect", [
    (True, "absent: not supplied in the intake"),
    ("yes", ["glaze log"]),
    (1, ["glaze log"]),
])
def test_only_absent_true_renders_as_absent(flag, expect):
    # D8: only the declared flag ``absent is True`` marks a slot absent.
    from app.factory.build.domain_pack import as_delivery_domain_pack
    pack = domain_pack_for(A)
    pack["fields"]["required_roles"] = dict(pack["fields"]["required_roles"],
                                            value=["glaze log"], absent=flag)
    assert as_delivery_domain_pack(pack)["required_roles"] == expect


# --- split A of #95: CI-M survivors in assert_pack (drop_rule, swallow_exception) ---

_EMPTY_INTAKE = SimpleNamespace(product_id="x", product_name="X", vertical="x_vertical",
                                summary="", capabilities=[], connectors=[])


@pytest.mark.parametrize("pack,reason", [
    ({}, r"^empty Domain Pack$"),
    ({"fields": {}}, r"^empty Domain Pack$"),
    (None, r"^empty Domain Pack$"),
], ids=["no-keys", "no-fields", "not-a-mapping"])
def test_an_empty_pack_fails_as_an_empty_pack(pack, reason):
    # The empty-pack rule is its own refusal: a pack with nothing in it is
    # refused for being empty, not left to fall through to a later check.
    with pytest.raises(DomainPackError, match=reason):
        assert_pack(pack)


@pytest.mark.parametrize("make,reason", [
    (lambda: {}, "empty Domain Pack"),
    (lambda: domain_pack_for(_EMPTY_INTAKE),
     "Domain Pack built from an empty intake: no domain_purpose, required_product_modules"),
], ids=["empty-pack", "empty-intake"])
def test_empty_pack_and_empty_intake_are_two_refusals(make, reason):
    with pytest.raises(DomainPackError) as err:
        assert_pack(make())
    assert str(err.value) == reason
