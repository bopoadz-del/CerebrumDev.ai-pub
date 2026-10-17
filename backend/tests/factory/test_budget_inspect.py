"""Stop-and-inspect: 30 min hard-stop, inspect, optional 45 min — never silent 2h."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.budget_inspect import (
    CEILING_S,
    STAGE_1_S,
    STAGE_2_S,
    inspect_build,
    inspect_decision,
    next_stage_wall,
    should_continue_after_inspect,
)
from app.factory.build.failure_kinds import TIMEOUT
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build_jobs import build_status


def _ledger(tmp_path: Path) -> BuildLedger:
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vetconnect", inputs_hash="abc")
    return ledger


def test_inspect_reads_caps_timeouts_stubs_and_contract_misses(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler site_visits (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "site_visits",
            "source": "coder LLM (kimi)",
            "done": 1,
            "total": 4,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler billing (deterministic contract template)",
        payload={
            "stage": "handlers",
            "capability": "billing",
            "source": "deterministic contract template",
            "done": 2,
            "total": 4,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="coder LLM timed out writing handler inventory",
        # The kind travels typed beside the message, as the runner records it
        # (failure_kinds.TIMEOUT from the exception type); the words decide nothing.
        payload={
            "stage": "coder",
            "capability": "inventory",
            "model_call": True,
            "failure_kind": TIMEOUT,
        },
    )
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail="unknown field(s): action",
        payload={"findings": ["billing puts 'action' inside the execute() payload"]},
    )
    snap = inspect_build(ledger, tmp_path / "build")
    assert snap["current_capability"] == "inventory"
    assert "site_visits" in snap["caps_written"]
    assert "billing" in snap["caps_templated"]
    assert snap["agent_written"] == 1
    assert snap["templated"] >= 1
    assert snap["stub_rate"] > 0
    assert any("timed out" in t for t in snap["timeouts"])
    assert any("action" in m for m in snap["contract_misses"])
    assert snap["pilot_ready"] is False
    assert any("pilot_ready is false" in b for b in snap["pilot_ready_blockers"])
    assert snap["progressing"] is True


def test_factory_grounded_emit_is_authored_not_a_stub(tmp_path):
    """sess_d5789a91 class: factory-grounded persist/event_bus lower stub_rate."""
    ledger = _ledger(tmp_path)
    for cap, source in (
        ("appointment_scheduling", "factory-grounded event_bus workflow"),
        ("clinic_dashboard", "factory-grounded persist"),
        ("pet_records_management", "factory-grounded persist"),
    ):
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} ({source})",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": source,
            },
        )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler leftover (deterministic contract template)",
        payload={
            "stage": "handlers",
            "capability": "leftover_gap",
            "source": "deterministic contract template",
        },
    )
    snap = inspect_build(ledger)
    assert "appointment_scheduling" in snap["caps_written"]
    assert "clinic_dashboard" in snap["caps_written"]
    assert "leftover_gap" in snap["caps_templated"]
    assert snap["stub_rate"] < 0.5
    assert snap["progressing"] is True
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] != "hard_stop"
    assert should_continue_after_inspect(snap) is True


def test_all_stubs_and_timeouts_are_not_progressing(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler billing (deterministic contract template)",
        payload={
            "stage": "handlers",
            "capability": "billing",
            "source": "deterministic contract template",
        },
    )
    ledger.append(
        EventKind.NOTE,
        detail="coder LLM timed out after 2400s",
        payload={"model_call": True, "capability": "inventory"},
    )
    snap = inspect_build(ledger)
    assert snap["progressing"] is False
    assert should_continue_after_inspect(snap) is False
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "hard_stop"
    assert decided["next_wall_s"] is None
    assert decided["continue"] is False
    assert "hard-stop" in decided["reason"]


def test_progressing_inspect_bumps_only_to_45_minutes(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler site_visits (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "site_visits",
            "source": "coder LLM (kimi)",
            "done": 3,
            "total": 6,
        },
    )
    snap = inspect_build(ledger)
    assert should_continue_after_inspect(snap) is True
    assert next_stage_wall(STAGE_1_S, STAGE_1_S, snap) == STAGE_2_S
    assert next_stage_wall(STAGE_2_S, STAGE_2_S, snap) is None
    assert next_stage_wall(STAGE_1_S, CEILING_S, snap) is None
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "continue_stage_2"
    assert decided["next_wall_s"] == STAGE_2_S
    assert CEILING_S not in (decided["next_wall_s"], decided["current_wall_s"])


def test_high_leftover_wall_is_inspected_not_cut(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler site_visits (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "site_visits",
            "source": "coder LLM (kimi)",
        },
    )
    snap = inspect_build(ledger)
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=CEILING_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "inspect_only_high_wall_honored"
    assert decided["next_wall_s"] is None
    assert decided["current_wall_s"] == CEILING_S


def test_runner_hard_stops_at_stage_1_without_progress(tmp_path):
    """A spent 30 min stage with only stubs must not bump to 2h or succeed."""
    now = {"t": 0.0}

    def clock():
        return now["t"]

    def hanging(ctx):
        ctx.note(
            "wrote handler billing (deterministic contract template)",
            stage="handlers",
            capability="billing",
            source="deterministic contract template",
            done=1,
            total=1,
        )
        now["t"] = STAGE_1_S + 5
        ctx.note(
            "stage wall reached — inspect",
            stage="budget",
            capability="billing",
            source="deterministic contract template",
        )
        from app.factory.build.roles_models import RoleResult

        return RoleResult(ok=True, detail="templated")

    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = hanging
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1, wall_clock_s=STAGE_1_S, phase_wall_clock_s=STAGE_1_S
        ),
        clock=clock,
        auto_pilot=True,
    )
    outcome = runner.run()
    assert outcome.ok is False
    assert outcome.outcome.value == "FAILED_BUDGET_SPENT"
    inspects = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("budget_inspect")
    ]
    assert inspects, "stage stop must emit an inspect snapshot"
    snap = inspects[-1].payload
    assert snap["decision"] == "hard_stop"
    assert snap.get("next_wall_s") is None
    assert runner.budget.wall_clock_s == STAGE_1_S
    assert runner.ledger.pilot_ready() is False
    status = build_status(tmp_path / "build")
    assert status["pilot_ready"] is False
    assert status["budget_inspect"]["decision"] == "hard_stop"
    assert "caps_written" in status["budget_inspect"]
    assert "stub_rate" in status["budget_inspect"]


def _wall_is_inspect_or_granted(runner) -> float:
    """The wall the INSPECT set, after checking any growth past it was granted.

    Since a granted REWORK round brings its own wall (runner._grant_rework_wall),
    a stub writer that fails its gate legitimately grows the wall past the
    staged ramp. That growth must be a ledgered ``wall_grant`` -- never the
    inspect. Returns the wall as it stood before the first grant (the
    inspect's), so the caller pins the staged ramp exactly.
    """
    grants = [
        (e.payload or {}).get("wall_grant")
        for e in runner.ledger.events()
        if (e.payload or {}).get("wall_grant")
    ]
    if not grants:
        return runner.budget.wall_clock_s
    assert runner.budget.wall_clock_s == grants[-1]["wall_s"], (
        "the wall grew past the inspect without a ledgered rework grant"
    )
    assert all(g["wall_s"] <= g["ceiling_s"] for g in grants if g["ceiling_s"])
    return grants[0]["wall_before_s"]


def test_runner_ramps_to_45m_only_when_inspect_sees_agent_work(tmp_path):
    now = {"t": 0.0}

    def clock():
        return now["t"]

    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.roles_models import RoleResult

    def writing(ctx):
        ctx.note(
            "wrote handler site_visits (coder LLM (kimi))",
            stage="handlers",
            capability="site_visits",
            source="coder LLM (kimi)",
            done=1,
            total=2,
        )
        now["t"] = STAGE_1_S + 1
        ctx.note(
            "wrote handler intake (coder LLM (kimi))",
            stage="handlers",
            capability="intake",
            source="coder LLM (kimi)",
            done=2,
            total=2,
        )
        return RoleResult(ok=True, detail="agent wrote")

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writing
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1, wall_clock_s=STAGE_1_S, phase_wall_clock_s=5400
        ),
        clock=clock,
        auto_pilot=True,
    )
    outcome = runner.run()
    inspects = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("budget_inspect")
    ]
    assert inspects
    decisions = [e.payload.get("decision") for e in inspects]
    assert "continue_stage_2" in decisions
    assert CEILING_S not in decisions
    # The inspect's own bump is the staged one -- 30 -> 45 min, never the
    # ceiling. (The replaced WRITER may fail its gate; a REWORK the rule
    # grants then brings its own wall, ledgered as a wall_grant -- that is
    # not the inspect, so it is checked apart.)
    bumps = [e.payload.get("next_wall_s") for e in inspects if e.payload.get("next_wall_s")]
    assert bumps == [STAGE_2_S]
    assert _wall_is_inspect_or_granted(runner) == STAGE_2_S
    # Replaced WRITER may fail later gates; the contract is the staged ramp.
    assert runner.ledger.pilot_ready() is False
    assert outcome.outcome is not None


def _inflight_cli_ledger(tmp_path: Path, *, deadline_s: float = 7230.0) -> BuildLedger:
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": deadline_s,
            "done": 0,
            "total": 1,
        },
    )
    return ledger


def test_inflight_cli_stage_1_inspect_bumps_to_45_not_unused(tmp_path):
    """sess_9d8e9a2: live CLI inside 7230s watchdog must not hard-stop as UNUSED."""
    ledger = _inflight_cli_ledger(tmp_path)
    snap = inspect_build(ledger)
    assert snap["cli_attempted"] is True
    assert snap["cli_in_flight"] is True
    assert snap["cli_finished"] is False
    assert snap["model_call_deadline_s"] == 7230.0
    assert snap["agent_written"] == 0
    decided = inspect_decision(
        elapsed_s=STAGE_1_S + 107.0,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "continue_stage_2"
    assert decided["next_wall_s"] == STAGE_2_S
    assert decided["decision"] != "hard_stop"
    assert "hard-stop" not in decided["reason"]
    assert "not FACTORY_CODE_CLI_UNUSED" in decided["reason"]
    assert "in-flight" in decided["reason"]
    assert "7230" in decided["reason"]


def test_inflight_cli_stage_2_inspect_waits_not_unused(tmp_path):
    ledger = _inflight_cli_ledger(tmp_path)
    snap = inspect_build(ledger)
    decided = inspect_decision(
        elapsed_s=STAGE_2_S,
        current_wall_s=STAGE_2_S,
        snapshot=snap,
        stage="stage_2",
    )
    assert decided["decision"] == "await_cli"
    assert decided["next_wall_s"] is None
    assert decided["decision"] != "hard_stop"
    assert "hard-stop" not in decided["reason"]
    assert "not FACTORY_CODE_CLI_UNUSED" in decided["reason"]


def test_inflight_cli_stage_2_progressing_bumps_ceiling_once(tmp_path):
    """Quiet 0-write kimi stays await_cli; WRITER 3/5 gets one ceiling bump."""
    ledger = _inflight_cli_ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler audit (coder LLM (kimi))",
        payload={
            "stage": "handlers",
            "capability": "audit",
            "source": "coder LLM (kimi)",
            "done": 3,
            "total": 5,
        },
    )
    snap = inspect_build(ledger)
    assert snap["cli_in_flight"] is True
    assert snap["agent_written"] >= 1
    decided = inspect_decision(
        elapsed_s=STAGE_2_S,
        current_wall_s=STAGE_2_S,
        snapshot=snap,
        stage="stage_2",
    )
    assert decided["decision"] == "continue_ceiling"
    assert decided["next_wall_s"] == CEILING_S
    assert decided["decision"] != "hard_stop"
    assert "FACTORY_CODE_CLI_UNUSED" not in decided["reason"] or (
        "not FACTORY_CODE_CLI_UNUSED" in decided["reason"]
    )
    assert "not a silent 2h grant" in decided["reason"]
    already = inspect_decision(
        elapsed_s=STAGE_2_S + 10.0,
        current_wall_s=CEILING_S,
        snapshot=snap,
        stage="wall",
    )
    assert already["decision"] == "inspect_only_high_wall_honored"
    assert already["next_wall_s"] is None


def test_unused_cli_after_wall_still_hard_stops(tmp_path, monkeypatch):
    from app.factory.build.coder_session import NAMED_BLOCKER_CLI_UNUSED

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("FACTORY_CODE_CLI", "/usr/local/bin/kimi")
    monkeypatch.setattr(
        "app.factory.build.coder_session.deepseek_cli_ready", lambda: True
    )
    ledger = _ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler billing (deterministic contract template)",
        payload={
            "stage": "handlers",
            "capability": "billing",
            "source": "deterministic contract template",
        },
    )
    snap = inspect_build(ledger)
    assert snap["cli_attempted"] is False
    assert snap["cli_in_flight"] is False
    decided = inspect_decision(
        elapsed_s=STAGE_1_S + 10.0,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "hard_stop"
    assert NAMED_BLOCKER_CLI_UNUSED in decided["reason"]
    assert "in-flight" not in decided["reason"]


def test_zero_harvest_after_cli_finish_hard_stops_not_unused_inflight(tmp_path, monkeypatch):
    from app.factory.build.coder_session import NAMED_BLOCKER_CLI_NO_AUTHORSHIP

    monkeypatch.setattr(
        "app.factory.build.coder_session.deepseek_cli_ready", lambda: True
    )
    ledger = _inflight_cli_ledger(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="FACTORY_CODE_CLI session finished",
        payload={"stage": "dispatch", "source": "coder CLI", "done": 1, "total": 1},
    )
    state = {
        "brief_dispatch": {
            "via": "cli",
            "ok": True,
            "cli_authored_ids": [],
            "handler_ids": [],
        }
    }
    snap = inspect_build(ledger, state=state)
    assert snap["cli_in_flight"] is False
    assert snap["cli_finished"] is True
    decided = inspect_decision(
        elapsed_s=STAGE_1_S + 10.0,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
        state=state,
    )
    assert decided["decision"] == "hard_stop"
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in decided["reason"]
    assert "in-flight" not in decided["reason"]


def test_runner_inflight_cli_stage_1_extends_wall_not_unused(tmp_path):
    now = {"t": 0.0}

    def clock():
        return now["t"]

    def hanging_cli(ctx):
        ctx.note(
            "dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
            stage="dispatch",
            source="coder CLI",
            model_call=True,
            deadline_s=7230,
            done=0,
            total=1,
        )
        now["t"] = STAGE_1_S + 5
        ctx.note(
            "kimi still running",
            stage="dispatch",
            source="coder CLI",
            model_call=True,
            deadline_s=7230,
        )
        from app.factory.build.roles_models import RoleResult

        return RoleResult(ok=True, detail="cli still in-flight")

    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = hanging_cli
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1, wall_clock_s=STAGE_1_S, phase_wall_clock_s=STAGE_1_S
        ),
        clock=clock,
        auto_pilot=True,
    )
    outcome = runner.run()
    inspects = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("budget_inspect")
    ]
    assert inspects, "stage-1 inspect must run while CLI is in-flight"
    snap = inspects[0].payload
    assert snap["decision"] == "continue_stage_2"
    assert snap.get("next_wall_s") == STAGE_2_S
    assert "hard-stop" not in str(snap.get("reason") or "")
    assert "not FACTORY_CODE_CLI_UNUSED" in str(snap.get("reason") or "")
    assert _wall_is_inspect_or_granted(runner) == STAGE_2_S
    extends = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("cli_wall_extended")
    ]
    assert extends, "Floor watchdog NOTE must follow the 30→45 bump"
    assert extends[0].payload.get("model_call") is True
    assert extends[0].payload.get("deadline_s")
    # WRITER returned; later gates may fail. Do not SUCCESS a thin pilot.
    assert runner.ledger.pilot_ready() is False
    assert outcome.ok is False


def test_cli_live_deadline_grows_with_inspect_bump_not_slash():
    from app.factory.build.coder_session import _cli_live_deadline
    from app.factory.build.roles_models import RoleContext

    started = time.monotonic()
    now = {"t": started}

    def clock():
        return now["t"]

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,  # type: ignore[arg-type]
        blueprint=None,
        plan=None,
        deadline=started + 1800.0,
        deadline_box={"at": started + 1800.0, "clock": clock},
    )
    first = _cli_live_deadline(ctx, 1785.0, started)
    assert first == pytest.approx(started + 1785.0, abs=1.0)
    ctx.deadline_box["at"] = started + 2700.0
    now["t"] = started + 1805.0
    bumped = _cli_live_deadline(ctx, 1785.0, started)
    assert bumped > first
    assert bumped == pytest.approx(now["t"] + (2700.0 - 1805.0) - 15.0, abs=1.0)


def test_cli_dispatch_timeout_tracks_factory_coder_timeout_not_1785(monkeypatch):
    """sess_2fba31ab: leftover stage-1 box must not freeze a 7200s pin at 1785."""
    from app.factory.build.coder_session import (
        cli_dispatch_timeout_s,
        cli_watchdog_remaining_s,
        _cli_live_deadline,
    )
    from app.factory.build.roles_models import RoleContext
    from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "7200")
    monkeypatch.delenv("FACTORY_CODER_ATTEMPT_WALL_S", raising=False)
    monkeypatch.delenv("FACTORY_CODER_BUDGET_S", raising=False)
    leftover = 1800.0
    timeout_s = cli_dispatch_timeout_s(leftover_s=leftover)
    assert timeout_s == pytest.approx(7200.0 + MODEL_CALL_GRACE_S)
    assert timeout_s >= 7200.0
    assert timeout_s != pytest.approx(STAGE_1_S - 15.0)
    assert timeout_s != 1785.0

    started = time.monotonic()
    now = {"t": started}

    def clock():
        return now["t"]

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,  # type: ignore[arg-type]
        blueprint=None,
        plan=None,
        deadline=started + leftover,
        deadline_box={"at": started + leftover, "clock": clock},
    )
    live = _cli_live_deadline(ctx, timeout_s, started)
    assert live == pytest.approx(started + timeout_s, abs=1.0)
    now["t"] = started + 1896.0
    still_open = _cli_live_deadline(ctx, timeout_s, started)
    assert still_open is not None
    assert still_open > now["t"]
    remaining = cli_watchdog_remaining_s(leftover_s=leftover - 1896.0, elapsed_s=1896.0)
    assert remaining > 1785.0
    assert remaining == pytest.approx(timeout_s - 1896.0, abs=1.0)


def test_cli_dispatch_timeout_test_scale_stays_tight(monkeypatch):
    from app.factory.build.coder_session import cli_dispatch_timeout_s

    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "0.2")
    monkeypatch.delenv("FACTORY_CODER_ATTEMPT_WALL_S", raising=False)
    assert cli_dispatch_timeout_s(leftover_s=5.0) == 30.0
    assert cli_dispatch_timeout_s(leftover_s=0.4) == 30.0


def test_stage_1_extend_does_not_slash_7200s_cli_watchdog(tmp_path, monkeypatch):
    """30→45 leftover-15 must not replace a 7230s Floor deadline with ~885s."""
    from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "7200")
    monkeypatch.delenv("FACTORY_CODER_ATTEMPT_WALL_S", raising=False)
    monkeypatch.delenv("FACTORY_CODER_BUDGET_S", raising=False)

    now = {"t": 0.0}

    def clock():
        return now["t"]

    def hanging_cli(ctx):
        from app.factory.build.coder_session import cli_dispatch_timeout_s

        ctx.note(
            "dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
            stage="dispatch",
            source="coder CLI",
            model_call=True,
            deadline_s=cli_dispatch_timeout_s(leftover_s=ctx.coder_time_left()),
            done=0,
            total=1,
        )
        now["t"] = STAGE_1_S + 5
        ctx.note("kimi still running", stage="dispatch", source="coder CLI")
        from app.factory.build.roles_models import RoleResult

        return RoleResult(ok=True, detail="cli still in-flight")

    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = hanging_cli
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1, wall_clock_s=STAGE_1_S, phase_wall_clock_s=STAGE_1_S
        ),
        clock=clock,
        auto_pilot=True,
    )
    runner.run()
    extends = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("cli_wall_extended")
    ]
    assert extends
    advertised = float(extends[0].payload.get("deadline_s"))
    expected = 7200.0 + MODEL_CALL_GRACE_S - (STAGE_1_S + 5)
    assert advertised == pytest.approx(expected, abs=2.0)
    assert advertised > 5000.0
    assert advertised != pytest.approx(STAGE_2_S - STAGE_1_S - 15.0, abs=5.0)
