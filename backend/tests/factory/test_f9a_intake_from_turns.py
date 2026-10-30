"""F-9a (split B of #95): the intake blueprint carries what the customer's
turns say, with the turn that says it -- never a Factory-invented default --
and validate_intake fails closed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.factory.blueprint import CapabilitySpec, ProductBlueprint
from app.factory.build import intake_blueprint as _ib
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

TURNS_A = [{"turn": 1, "role": "user", "text": "kiln log please"},
           {"turn": 2, "role": "user", "text": "glazers and kiln techs use it"}]
TURNS_B = [{"turn": 1, "role": "user", "text": "glazers only, from turn one"}]


@pytest.mark.parametrize("turns,expect", [(TURNS_A, 2), (TURNS_B, 1)], ids=["A", "B"])
def test_provenance_is_the_turn_that_says_it(turns, expect):
    pack = {"primary_users": ["glazers"]}
    intake = intake_from_product_blueprint(A, chat_turns=turns, domain_pack=pack)
    assert intake["users"]["source_turn"] == expect
    assert "glazers" in intake["users"]["source_text"]


@pytest.mark.parametrize("role", ["harbourmaster", "tug pilot"])
def test_value_no_turn_says_is_not_stamped_on_a_turn(role):
    intake = intake_from_product_blueprint(
        A, chat_turns=TURNS_A, domain_pack={"required_roles": [role]})
    assert intake["roles"]["value"] == [role]
    assert intake["roles"]["source_turn"] is None
    assert intake["roles"]["source"] == "architect"
    assert intake["users"]["absent"] is True and intake["users"]["value"] == []


def test_fabricated_turn_provenance_is_refused():
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"primary_users": ["glazers"]})
    intake["users"] = dict(intake["users"], value=["harbourmaster"])
    with pytest.raises(IntakeBlueprintError, match="users"):
        validate_intake(intake, chat_turns=TURNS_A)


@pytest.mark.parametrize("turns", [TURNS_A, TURNS_B], ids=["A", "B"])
def test_no_made_up_roles_users_or_sources_come_back_on_intake(turns):
    intake = intake_from_product_blueprint(A, chat_turns=turns, domain_pack={})
    for key in ("roles", "users", "data_sources", "integrations", "constraints", "done_when"):
        assert intake[key]["value"] == [], key
        assert intake[key]["absent"] is True, key


def test_validate_intake_refuses_empty_value_not_declared_absent():
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={})
    validate_intake(intake, chat_turns=TURNS_A)
    intake["users"] = {k: v for k, v in intake["users"].items() if k != "absent"}
    with pytest.raises(IntakeBlueprintError, match="users.value is empty but not declared absent"):
        validate_intake(intake)


def test_real_intake_path_checks_citations(monkeypatch):
    real = _ib._provenance

    def lying(values, turns):
        if not values:
            return real(values, turns)
        return {"value": values, "source_turn": 1, "source_text": "", "source": "turn"}
    monkeypatch.setattr(_ib, "_provenance", lying)
    with pytest.raises(IntakeBlueprintError, match="cites turn 1"):
        intake_from_product_blueprint(A, chat_turns=TURNS_A,
                                      domain_pack={"primary_users": ["glazers"]})


@pytest.mark.parametrize("bp,expect", [(A, None), (B, ["ais_feed"])], ids=["A", "B"])
def test_intake_integrations_fall_back_to_blueprint_connectors(bp, expect):
    intake = intake_from_product_blueprint(bp, chat_turns=TURNS_A, domain_pack={})
    if expect is None:
        assert intake["integrations"]["absent"] is True
    else:
        assert intake["integrations"]["value"] == expect


@pytest.mark.parametrize("said,typed,turn", [
    ("GLAZERS run the kiln", "Glazers", 2),
    ("kiln techs only", "KILN TECHS", 1),
])
def test_provenance_matching_is_case_insensitive(said, typed, turn):
    turns = [{"turn": 1, "role": "user", "text": "hello" if turn == 2 else said},
             {"turn": 2, "role": "user", "text": said if turn == 2 else "thanks"}]
    intake = intake_from_product_blueprint(A, chat_turns=turns,
                                           domain_pack={"primary_users": [typed]})
    assert intake["users"]["source_turn"] == turn
    assert intake["users"]["source"] == "turn"
    validate_intake(intake, chat_turns=turns)


def test_citation_must_say_every_value_not_just_one():
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"primary_users": ["glazers"]})
    intake["users"] = dict(intake["users"], value=["glazers", "harbourmaster"])
    with pytest.raises(IntakeBlueprintError, match="users cites turn 2"):
        validate_intake(intake, chat_turns=TURNS_A)


@pytest.mark.parametrize("values,turn,source", [(["glazers"], 2, "turn"),
                                                (["harbourmaster"], None, "architect")])
def test_provenance_picks_the_saying_turn_or_none(values, turn, source):
    got = _ib._provenance(values, TURNS_A)
    assert got["source_turn"] == turn
    assert got["source"] == source


@pytest.mark.parametrize("values,turn,source", [
    (["glazers", "kiln techs"], 2, "turn"),        # one turn says both
    (["glazers", "harbourmaster"], None, "architect"),  # no turn says both
], ids=["both-said", "one-said"])
def test_provenance_needs_one_turn_saying_every_value(values, turn, source):
    # I2: a turn that says only some of the values is not the source.
    got = _ib._provenance(values, TURNS_A)
    assert got["source_turn"] == turn
    assert got["source"] == source


@pytest.mark.parametrize("key,field,value", [
    ("target_users", "users", ["glazers"]),
    ("primary_users", "users", ["kiln techs"]),
    ("security_regulatory_rules", "constraints", ["glazers sign each firing"]),
    ("domain_rules", "constraints", ["kiln techs log cones"]),
])
def test_intake_reads_each_listed_key(key, field, value):
    # I8 / I10: the fallback key on its own fills the intake field.
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={key: value})
    assert intake[field]["value"] == value
    assert intake[field].get("absent") is not True


def test_intake_without_fallback_keys_declares_them_absent():
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={})
    assert intake["users"]["absent"] is True
    assert intake["constraints"]["absent"] is True


@pytest.mark.parametrize("cited", [7, 99])
def test_citing_a_turn_that_does_not_exist_is_refused(cited):
    # I13: a source_turn no chat turn carries is a fabricated citation.
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"primary_users": ["glazers"]})
    validate_intake(intake, chat_turns=TURNS_A)
    intake["users"] = dict(intake["users"], source_turn=cited)
    with pytest.raises(IntakeBlueprintError, match=f"users cites turn {cited}"):
        validate_intake(intake, chat_turns=TURNS_A)


@pytest.mark.parametrize("bad", ["glazers", ("glazers",)])
def test_intake_field_value_must_be_a_list(bad):
    # I17: a non-list value is refused even when the cited turn says it.
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"primary_users": ["glazers"]})
    intake["users"] = dict(intake["users"], value=bad)
    with pytest.raises(IntakeBlueprintError, match=r"users\.value must be a list"):
        validate_intake(intake, chat_turns=TURNS_A)


# --- split B of #95: CI-M survivors (drop_rule, swallow_exception) -----------

@pytest.mark.parametrize("kwargs,turn,users_turn", [
    (dict(brief="glazers fire the kiln"), 1, 1),
    (dict(chat_turns=[{"turn": 3, "role": "user", "text": "kiln techs fire it"}]), 3, None),
], ids=["brief-only", "chat-turn-3"])
def test_a_brief_alone_becomes_turn_one(kwargs, turn, users_turn):
    # intake_from_product_blueprint: with no chat turns, the brief IS turn 1;
    # every capability and every said value cites it.
    intake = intake_from_product_blueprint(A, domain_pack={"primary_users": ["glazers"]}, **kwargs)
    assert [c["source_turn"] for c in intake["capabilities"]] == [turn, turn]
    assert intake["users"]["source_turn"] == users_turn
    assert intake["users"]["source"] == ("turn" if users_turn else "architect")


class _NoCaps:
    product_id = "x"
    product_name = "X"
    vertical = "x_vertical"
    summary = "s"
    capabilities = ()
    connectors = ()


@pytest.mark.parametrize("plan,ids", [
    (None, None),
    (SimpleNamespace(capabilities=(SimpleNamespace(id="kiln_slot"),)), ["kiln_slot"]),
], ids=["no-roster", "plan-roster"])
def test_no_capabilities_anywhere_is_refused(plan, ids):
    if ids is None:
        with pytest.raises(IntakeBlueprintError) as err:
            intake_from_product_blueprint(_NoCaps(), plan=plan, brief="b")
        assert str(err.value) == "intake blueprint has no capabilities"
    else:
        intake = intake_from_product_blueprint(_NoCaps(), plan=plan, brief="b")
        assert [c["id"] for c in intake["capabilities"]] == ids


@pytest.mark.parametrize("bad", [["not", "a", "mapping"], "intake"], ids=["list", "str"])
def test_validate_intake_refuses_a_non_mapping(bad):
    with pytest.raises(IntakeBlueprintError) as err:
        validate_intake(bad)
    assert str(err.value) == "intake blueprint must be a mapping"


@pytest.mark.parametrize("turns,users", [(TURNS_A, ["glazers"]), (TURNS_B, ["glazers"])],
                         ids=["A", "B"])
def test_validate_intake_accepts_a_real_intake_mapping(turns, users):
    intake = intake_from_product_blueprint(B, chat_turns=turns, domain_pack={"primary_users": users})
    assert validate_intake(intake, chat_turns=turns) == intake
    assert intake["users"]["source_turn"] == turns[-1]["turn"]


@pytest.mark.parametrize("key,field,value", [
    ("target_users", "users", "glazers"),
    ("required_roles", "roles", "kiln techs"),
    ("domain_rules", "constraints", "cone 6 only"),
], ids=["users", "roles", "constraints"])
def test_a_single_string_is_one_listed_value(key, field, value):
    # _listed: a bare string is ONE value, never split into its characters.
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={key: value})
    assert intake[field]["value"] == [value]


# --- repair 1 of split B ------------------------------------------------------

@pytest.mark.parametrize("pad,want_len", [(600, 400), (10, None)], ids=["long", "short"])
def test_source_text_is_cut_at_400(pad, want_len):
    # M3: the cited turn's text is kept to 400 characters.
    text = "glazers " + "k" * pad
    turns = [{"turn": 1, "role": "user", "text": text}]
    intake = intake_from_product_blueprint(A, chat_turns=turns, domain_pack={"primary_users": ["glazers"]})
    got = intake["users"]["source_text"]
    assert len(got) == (want_len or len(text))
    assert got == text[:400]


@pytest.mark.parametrize("connectors,want", [
    (["ais_feed", "  ", ""], ["ais_feed"]),
    (["\t", "tide_api"], ["tide_api"]),
], ids=["trailing-blank", "leading-blank"])
def test_blank_blueprint_connectors_are_dropped(connectors, want):
    # M10: the blueprint-connector fallback drops blank strings.
    bp = SimpleNamespace(product_id="x", product_name="X", vertical="v", summary="s",
                         capabilities=[SimpleNamespace(id="berth_plan")], connectors=connectors)
    intake = intake_from_product_blueprint(bp, chat_turns=TURNS_A, domain_pack={})
    assert intake["integrations"]["value"] == want


@pytest.mark.parametrize("value,turn", [("  glazers  ", 2), ("\tkiln log ", 1)], ids=["t2", "t1"])
def test_padded_value_is_credited_to_its_turn(value, turn):
    # M12: _said_in strips the value before matching.
    got = _ib._provenance([value], TURNS_A)
    assert got["source"] == "turn"
    assert got["source_turn"] == turn


# --- grader-miss fix of split B: a blank listed value is no value ------------

@pytest.mark.parametrize("roles,want", [
    (["  "], None),
    (["\t\n", " "], None),
    (["kiln techs"], ["kiln techs"]),
], ids=["spaces", "tab-newline", "real"])
def test_blank_listed_value_is_declared_absent(roles, want):
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"required_roles": roles})
    field = validate_intake(intake, chat_turns=TURNS_A)["roles"]
    if want is None:
        assert field.get("absent") is True and field["value"] == []
    else:
        assert field["value"] == want and field.get("absent") is not True


# --- G6 post-hoc #98 (98-M8): the pack's own required_connectors win over the
# blueprint's connectors; the blueprint list is only the fallback.

@pytest.mark.parametrize("pack, want", [
    ({"required_connectors": ["tide_gauge"]}, ["tide_gauge"]),
    ({}, ["ais_feed"]),
])
def test_pack_required_connectors_beat_blueprint_connectors(pack, want):
    intake = intake_from_product_blueprint(B, chat_turns=TURNS_A, domain_pack=pack)
    assert intake["integrations"]["value"] == want


# --- G6 post-hoc #98 (98-M18): validate_intake without chat_turns (the
# brief_compiler path) accepts a field that cites a turn; with turns it checks.

@pytest.mark.parametrize("turns, refused", [(None, False), ([TURNS_A[0]], True)])
def test_cited_turn_is_checked_only_when_turns_are_given(turns, refused):
    intake = intake_from_product_blueprint(A, chat_turns=TURNS_A, domain_pack={"primary_users": ["glazers"]})
    assert intake["users"]["source_turn"] == 2
    try:
        validate_intake(intake, chat_turns=turns)
        got = "accepted"
    except IntakeBlueprintError:
        got = "refused"
    except TypeError as exc:  # None turns must never be iterated
        got = f"crashed: {type(exc).__name__}"
    assert got == ("refused" if refused else "accepted")


# --- G2 handback L2: a capability's source_text is the customer's words, cut
# at 400 characters.

@pytest.mark.parametrize("length", [50, 500])
def test_capability_source_text_is_cut_at_400(length):
    words = ("kiln firing " * 60)[:length]
    intake = intake_from_product_blueprint(A, chat_turns=[{"turn": 1, "role": "user", "text": words}])
    assert intake["capabilities"][0]["source_text"] == words[:400]
    assert len(intake["capabilities"][0]["source_text"]) == min(length, 400)
