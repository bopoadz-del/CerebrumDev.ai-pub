"""The bar comes from the brief: the BUILD LEVEL is the user's typed choice.

Owner's order (2026-10-05): the Floor asks the build level (prototype / light
/ pilot / production) as a typed field, saved on the session and the
blueprint, rendered into the writer brief's exit condition and the
CODE -> PRODUCT -> STORE ladder. Prototype is DONE at CODE_GREEN; "thin
SUCCESS is a failure" applies from pilot up; production adds the acceptance
floor in full. No default, never inferred from wording; Approve is refused
with a typed reason until the user chooses.

The headline test builds the SAME brief at prototype and at pilot and reads
each run's stop gate back out of its own ledger.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.factory.blueprint import ProductBlueprint, load_blueprint
from app.factory.build.build_level import (
    BARS,
    BuildLevel,
    BuildLevelError,
    bar_for,
    declared_level,
    ledger_bar,
    parse_build_level,
    render_exit_condition,
)
from app.factory.build.ledger import EventKind
from app.models.session import ProductDesignState, SessionState

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _with_level(bp: ProductBlueprint, level):
    data = bp.model_dump(mode="json")
    data["build_level"] = level
    return ProductBlueprint.model_validate(data)


# -- the level: typed, closed, no default ------------------------------------


def test_there_is_no_default_level():
    bp = load_blueprint(SMOKE)
    assert bp.build_level is None
    assert declared_level(bp) is None and bar_for(bp) is None
    assert ProductDesignState().build_level is None


def test_a_level_outside_the_four_is_refused_never_mapped():
    with pytest.raises(BuildLevelError):
        parse_build_level("standard")
    with pytest.raises(BuildLevelError):
        parse_build_level("")
    with pytest.raises(ValidationError):
        _with_level(load_blueprint(SMOKE), "zorblat-grade")


def test_the_retired_rigor_field_is_dropped_not_translated():
    """A stored blueprint's old ``rigor`` (defaulted to production, never the
    user's choice) loads, and does not become a level."""
    data = load_blueprint(SMOKE).model_dump(mode="json")
    data["rigor"] = "production"
    bp = ProductBlueprint.model_validate(data)
    assert bp.build_level is None
    assert "rigor" not in bp.model_dump(mode="json")


def test_the_ladder_each_level_climbs():
    assert BARS[BuildLevel.PROTOTYPE].stop_gate == "CODE"
    assert not BARS[BuildLevel.PROTOTYPE].reaches_pilot
    for level in (BuildLevel.LIGHT, BuildLevel.PILOT, BuildLevel.PRODUCTION):
        assert BARS[level].stop_gate == "STORE"
    # Thin SUCCESS is a failure only at pilot and above.
    assert [lv.value for lv in BuildLevel if BARS[lv].thin_success_is_failure] == [
        "pilot",
        "production",
    ]
    # Production alone adds the acceptance floor in full.
    assert [lv.value for lv in BuildLevel if BARS[lv].full_floor] == ["production"]


def test_production_adds_the_floor_in_full():
    from app.factory.build.acceptance_floor import advisory_ids, enforced_ids

    bp = load_blueprint(SMOKE)
    full = set(enforced_ids(_with_level(bp, "production")))
    for level in ("prototype", "light", "pilot"):
        lowered = set(enforced_ids(_with_level(bp, level)))
        assert lowered < full, level
        assert (full - lowered) <= set(advisory_ids(_with_level(bp, level))), level
    # Undeclared is never lowered.
    assert set(enforced_ids(bp)) == full


# -- the writer brief: exit condition + ladder per level ---------------------


def test_the_exit_condition_is_rendered_for_the_chosen_level():
    bp = load_blueprint(SMOKE)
    proto = render_exit_condition(_with_level(bp, "prototype"))
    pilot = render_exit_condition(_with_level(bp, "pilot"))
    prod = render_exit_condition(_with_level(bp, "production"))
    assert "BUILD LEVEL: prototype" in proto
    assert "DONE at CODE_GREEN" in proto
    assert "thin SUCCESS is a failure" not in proto
    assert "BUILD LEVEL: pilot" in pilot
    assert "thin SUCCESS is a failure" in pilot
    assert "advisory" in pilot
    assert "applies in full" in prod
    for text in (proto, pilot, prod):
        assert "CODE ->" in text and "PRODUCT ->" in text and "STORE ->" in text


def test_the_start_message_states_the_stop_gate_and_when_download_unlocks():
    from app.factory.build.build_level import start_expectation

    bp = load_blueprint(SMOKE)
    for level in BuildLevel:
        text = start_expectation(_with_level(bp, level.value))
        gate = BARS[level].stop_gate
        assert f"stops at the {gate} gate" in text, level
        assert f"Download unlocks only when the {gate} gate is green" in text, level
    assert "(CODE_GREEN)" in start_expectation(_with_level(bp, "prototype"))
    assert "(STORE_GREEN)" in start_expectation(_with_level(bp, "light"))
    assert "download" in start_expectation(bp).lower()


def test_the_compiled_brief_owes_only_the_rungs_its_level_climbs():
    from app.factory.build.brief_compiler import compile_brief
    from app.factory.build.brief_lint import lint_brief
    from app.factory.product_architect import plan_blueprint

    bp = load_blueprint(SMOKE)
    plan = plan_blueprint(bp)
    proto = compile_brief(_with_level(bp, "prototype"), plan)
    pilot = compile_brief(_with_level(bp, "pilot"), plan)
    for compiled in (proto, pilot):
        assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert "BUILD LEVEL: prototype" in proto.text
    assert "[check:build_level]" in proto.text
    assert "[check:store_gate]" not in proto.text
    assert "thin SUCCESS" not in proto.text
    assert "BUILD LEVEL: pilot" in pilot.text
    assert "[check:store_gate]" in pilot.text
    assert "thin SUCCESS" in pilot.text


# -- the headline: same brief, two levels, two stop points -------------------


def _level_notes(ledger):
    return [
        e for e in ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("build_level")
    ]


def test_the_same_brief_at_prototype_and_at_pilot_stops_at_different_gates(
    tmp_path, monkeypatch, stub_coder
):
    """The level -- not the deployment's FACTORY_AUTO_PILOT -- decides where a
    run stops. The environment is set AGAINST each level to prove it."""
    from app.factory.build.level_grade import three_gate_verdict
    from app.factory.build.runner import BuildBudget, Outcome, RoleRunner
    from app.factory.build_jobs import build_status

    brief = load_blueprint(SMOKE)
    budget = BuildBudget(max_rework=1, wall_clock_s=900, phase_wall_clock_s=600)

    # prototype, with the environment asking for a pilot cycle
    monkeypatch.setenv("FACTORY_AUTO_PILOT", "1")
    proto = RoleRunner(_with_level(brief, "prototype"), tmp_path / "proto", budget=budget)
    proto_outcome = proto.run()
    assert proto_outcome.outcome is Outcome.SUCCESS, proto_outcome.to_dict()
    p_events = proto.ledger.events()
    assert EventKind.PILOT_OPENED not in [e.kind for e in p_events]
    p_note = _level_notes(proto.ledger)[0]
    assert p_note.payload["build_level"] == "prototype"
    assert p_note.payload["stop_gate"] == "CODE"
    p_term = proto.ledger.terminal_event()
    assert p_term.kind is EventKind.RUN_SUCCEEDED
    assert p_term.payload["stop_gate"] == "CODE"
    assert p_term.payload["stopped_at"] == "CODE"
    assert proto.ledger.pilot_ready() is False
    p_status = build_status(tmp_path / "proto")
    assert three_gate_verdict(p_status) == {
        "CODE": "PASS",
        "PRODUCT": "NOT_RUN",
        "STORE": "NOT_RUN",
    }
    assert p_status["build_level"] == {"build_level": "prototype", "stop_gate": "CODE"}

    # pilot, with the environment asking for code-only
    monkeypatch.setenv("FACTORY_AUTO_PILOT", "0")
    pilot = RoleRunner(_with_level(brief, "pilot"), tmp_path / "pilot", budget=budget)
    pilot.run()
    l_events = pilot.ledger.events()
    # The level climbed past CODE: a pilot cycle opened on the same workspace,
    # and the ledger never recorded a code-only SUCCESS.
    assert EventKind.PILOT_OPENED in [e.kind for e in l_events]
    l_note = _level_notes(pilot.ledger)[0]
    assert l_note.payload["build_level"] == "pilot"
    assert l_note.payload["stop_gate"] == "STORE"
    l_term = pilot.ledger.terminal_event()
    assert l_term.payload["build_level"] == "pilot"
    assert l_term.payload["stop_gate"] == "STORE"
    assert all(
        (e.payload or {}).get("cycle") == "pilot"
        for e in l_events
        if e.kind is EventKind.RUN_SUCCEEDED
    )
    assert build_status(tmp_path / "pilot")["build_level"] == {
        "build_level": "pilot",
        "stop_gate": "STORE",
    }

    # Two levels, two stop points -- each stated in its own ledger.
    assert p_note.payload["stop_gate"] != l_note.payload["stop_gate"]
    print(
        "\nBUILD-LEVEL TEST:",
        json.dumps(
            {
                "prototype": {
                    "stop_gate": p_term.payload["stop_gate"],
                    "stopped_at": p_term.payload["stopped_at"],
                    "outcome": p_term.payload["outcome"],
                },
                "pilot": {
                    "stop_gate": l_term.payload["stop_gate"],
                    "stopped_at": l_term.payload.get("stopped_at"),
                    "outcome": l_term.payload["outcome"],
                    "pilot_opened": True,
                },
            }
        ),
    )


def test_ledger_bar_reads_the_level_the_run_recorded():
    class _E:
        def __init__(self, payload):
            self.payload = payload

    assert ledger_bar([_E({}), _E({"cycle": "code"})]) is None
    assert ledger_bar([_E({"build_level": "light", "stop_gate": "STORE"})]) == {
        "build_level": "light",
        "stop_gate": "STORE",
    }


# -- the Floor: typed intake, confirm-only, refusal without a level ----------


def _state() -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    s = SessionState(session_id="sess-build-level", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    bp = draft_blueprint_from_brief("build a zorblat platform for my quux business")
    s.product_design.blueprint = bp.model_dump(mode="json")
    return s


@pytest.fixture
def session():
    from app.core import session_store

    state = _state()
    session_store._session_store[state.session_id] = state
    yield state
    session_store._session_store.pop(state.session_id, None)


async def _events(session_id, message="", action=None, value=None):
    from app.factory.floor_actions import parse_action
    from app.routers import chat as chat_router

    out = []
    async for raw in chat_router._stream_response(session_id, message, parse_action(action), value):
        ev = {"event": "", "data": ""}
        for line in [x for x in raw.strip().splitlines() if x]:
            if line.startswith("event:"):
                ev["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line.split(":", 1)[1].strip())
        out.append(ev)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "continue", "run_pilot"])
async def test_a_run_action_without_a_level_is_refused_with_a_typed_reason(
    session, monkeypatch, action
):
    from app.factory import platform_chat_flow

    started = []
    for name in ("approve_and_generate", "start_or_resume_coder", "resume_pilot_cycle"):
        monkeypatch.setattr(platform_chat_flow, name, lambda *a, _n=name, **k: started.append(_n) or {})
    events = await _events(session.session_id, action=action)
    kinds = [e["event"] for e in events]
    assert started == []
    assert "generation" not in kinds
    info = next(json.loads(e["data"]) for e in events if e["event"] == "info")
    assert info["refused"] == "BUILD_LEVEL_REQUIRED"
    assert info["awaiting_action"] == "set_build_level"
    intake = next(json.loads(e["data"]) for e in events if e["event"] == "intake")
    assert intake["declared"]["build_level"] is None
    assert session.product_design.blueprint_approved is False


@pytest.mark.asyncio
async def test_change_proposes_the_level_and_only_confirm_stores_it(session):
    events = await _events(session.session_id, action="set_build_level", value="prototype")
    # Change alone stores nothing: the level is on the proposal.
    assert session.product_design.build_level is None
    assert session.product_design.blueprint.get("build_level") is None
    intake = next(json.loads(e["data"]) for e in events if e["event"] == "intake")
    assert intake["proposal"] == {"build_level": "prototype"}
    assert intake["declared"]["build_level"] is None
    # Confirm: saved on the session AND on the blueprint, before approval.
    events = await _events(session.session_id, action="confirm_intake")
    assert session.product_design.build_level == "prototype"
    assert session.product_design.blueprint["build_level"] == "prototype"
    intake = next(json.loads(e["data"]) for e in events if e["event"] == "intake")
    assert intake["declared"]["build_level"] == "prototype"


@pytest.mark.asyncio
async def test_the_level_is_frozen_once_the_blueprint_is_approved(session):
    session.product_design.intake_proposal = {"build_level": "pilot"}
    await _events(session.session_id, action="confirm_intake")
    session.product_design.blueprint_approved = True
    await _events(session.session_id, action="set_build_level", value="prototype")
    await _events(session.session_id, action="confirm_intake")
    assert session.product_design.build_level == "pilot"
    assert session.product_design.blueprint["build_level"] == "pilot"


def test_the_level_is_part_of_the_hashed_blueprint():
    from app.factory.build.runner import blueprint_hash

    bp = load_blueprint(SMOKE)
    assert blueprint_hash(_with_level(bp, "prototype")) != blueprint_hash(
        _with_level(bp, "pilot")
    )


def test_the_model_proposal_alone_stores_nothing(session):
    """The chat model's answer is a PROPOSAL: the typed fields stay unset."""
    from app.factory import platform_chat_llm

    decision = {
        "action": "reply",
        "message": "Shall I use these?",
        "brief": "",
        "refine": {"op": "set_vertical", "value": "zorblat_yards"},
        "connectors": [],
        "missing_connectors": [],
        "intake": {"country": "zq", "currency": "zqx", "build_level": "pilot"},
    }
    result = platform_chat_llm.apply_decision(session, "zq, zqx, pilot please", decision)
    pd = session.product_design
    assert (pd.vertical, pd.country, pd.currency, pd.build_level) == (None, None, None, None)
    assert session.product_design.blueprint.get("build_level") is None
    assert result["intake"]["proposal"] == {
        "vertical": "zorblat_yards",
        "country": "ZQ",
        "currency": "ZQX",
        "build_level": "pilot",
    }
    assert result["intake"]["declared"]["build_level"] is None


def test_a_proposed_level_outside_the_four_is_dropped_not_mapped(session):
    from app.factory import platform_chat_llm

    platform_chat_llm.record_intake_proposal(
        session, {"intake": {"build_level": "standard", "country": "zq"}}
    )
    assert session.product_design.intake_proposal == {"country": "ZQ"}


@pytest.mark.asyncio
async def test_confirm_intake_stores_every_proposed_field(session):
    session.product_design.intake_proposal = {
        "vertical": "zorblat_yards",
        "country": "ZQ",
        "currency": "ZQX",
        "build_level": "light",
    }
    events = await _events(session.session_id, action="confirm_intake")
    pd = session.product_design
    assert pd.vertical == "zorblat_yards"
    assert (pd.country, pd.currency, pd.build_level) == ("ZQ", "ZQX", "light")
    assert pd.intake_proposal is None
    assert pd.blueprint["build_level"] == "light"
    assert pd.blueprint["locale"] == {"country": "ZQ", "currency": "ZQX"}
    intake = next(json.loads(e["data"]) for e in events if e["event"] == "intake")
    assert intake["proposal"] is None
    assert intake["declared"]["build_level"] == "light"


@pytest.mark.asyncio
async def test_confirm_with_nothing_proposed_stores_nothing(session):
    await _events(session.session_id, action="confirm_intake")
    assert session.product_design.build_level is None


@pytest.mark.asyncio
async def test_a_level_lets_approve_through(session, monkeypatch):
    from app.factory import platform_chat_flow

    monkeypatch.setattr(
        platform_chat_flow,
        "approve_and_generate",
        lambda *a, **k: {"ok": True, "summary": "Build started.", "product_id": "p1"},
    )
    await _events(session.session_id, action="set_build_level", value="pilot")
    refused = await _events(session.session_id, action="approve")
    assert "generation" not in [e["event"] for e in refused]  # proposed, not confirmed
    await _events(session.session_id, action="confirm_intake")
    events = await _events(session.session_id, action="approve")
    assert [e["event"] for e in events].count("generation") == 1


def test_a_prototype_does_not_open_a_pilot_cycle_on_continue(session, monkeypatch):
    """Prototype is DONE at CODE_GREEN: continuing it is refused at its
    level, not silently climbed."""
    from app.factory import platform_chat_flow

    session.product_design.build_level = "prototype"
    session.product_design.blueprint_approved = True
    monkeypatch.setattr(platform_chat_flow, "is_pilot_ready", lambda *a, **k: False)

    def _boom(*a, **k):
        raise AssertionError("a prototype must not open a pilot cycle")

    monkeypatch.setattr(platform_chat_flow, "generate_product", _boom)
    result = platform_chat_flow.resume_pilot_cycle(session)
    assert result["already_complete"] is True
