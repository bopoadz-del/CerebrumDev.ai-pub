"""The build wall follows the rework rounds the rule grants; a closed call reads closed.

Live 2026-10-06 (01a1eed7, post-deploy smoke 37515169785, session
sess_f46da9fe15ff409a): a production build passed WRITER, failed TESTER,
reworked WRITER once (round 1 of 2, build 1 of 6 -- inside every budget) and
was stopped at the next phase: "wall-clock budget of 2700s spent before TESTER
completed; inspect wall: FACTORY_CODE_CLI in-flight (1800s watchdog) -- wait".
Two causes, both here:

1. The WRITER relay copied only {model_call, deadline_s, provider} onto its
   ledger NOTE and dropped the worker's ``model_call_state=closed``, so the
   codewhale call read as open forever (the build's budget_inspect said
   cli_in_flight=true, cli_finished=false after "codewhale writer CLI exited").
2. A granted REWORK round brought no wall time with it, so a run inside the
   owner's rework budgets hit a fixed whole-build wall.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest import mock

from app.factory.blueprint import load_blueprint
from app.factory.build import budget_inspect, codewhale_worker
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateResult
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.model_call import CLOSED, MODEL_CALL_STATE
from app.factory.build.roles_handlers import RELAYED_CALL_FIELDS, relayed_call_fields
from app.factory.build.runner import BuildBudget, Outcome, RoleRunner
from app.factory.build.tenant_bind import bind_tenant_store

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


class _QuickCli:
    """A codewhale process that finishes at once."""

    def __init__(self):
        self.stdout = io.StringIO(
            json.dumps({"status": "completed", "termination_reason": "resolved"}) + "\n"
        )
        self.stderr = io.StringIO("")
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = 9


# -- 1. the close reaches the ledger ---------------------------------------


def test_the_relay_keeps_the_model_call_state():
    assert MODEL_CALL_STATE in RELAYED_CALL_FIELDS
    closed = {"model_call": False, MODEL_CALL_STATE: CLOSED, "provider": "deepseek"}
    assert relayed_call_fields(closed)[MODEL_CALL_STATE] == CLOSED
    assert relayed_call_fields(None) == {}


def test_a_finished_codewhale_call_does_not_read_as_in_flight(tmp_path):
    """The real worker's open and close notes, relayed into a real ledger by
    the writer's own field selection: after the CLI exits, the inspector must
    not see a call in flight (live: cli_in_flight=true after the exit)."""
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")

    def relay(line, info):
        ledger.append(
            EventKind.NOTE,
            detail=line[:200],
            payload={"source": "codewhale_worker", **relayed_call_fields(info)},
        )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen",
                           side_effect=lambda *a, **k: _QuickCli()):
        codewhale_worker.run_worker_job(
            "write the platform",
            workspace,
            tenant_store=bind_tenant_store("acct-wall"),
            progress=relay,
            timeout_s=30.0,
        )

    events = list(ledger.events())
    assert any((e.payload or {}).get("model_call") for e in events), "no open note"
    flight = budget_inspect._cli_flight(events, {})
    assert flight["cli_in_flight"] is False
    assert flight["cli_finished"] is True

    # Control: the field list the relay used before (no model_call_state)
    # leaves the same call open forever -- the live defect.
    old_fields = ("model_call", "deadline_s", "provider")
    stale = BuildLedger(tmp_path / "stale.jsonl")
    for e in events:
        p = e.payload or {}
        stale.append(
            EventKind.NOTE,
            detail=e.detail,
            payload={"source": p.get("source"), **{k: p[k] for k in old_fields if p.get(k) is not None}},
        )
    assert budget_inspect._cli_flight(list(stale.events()), {})["cli_in_flight"] is True


class _Ev:
    def __init__(self, payload):
        self.payload = payload


def test_a_second_call_after_a_closed_one_is_in_flight():
    """A rework round re-dispatches the writer: the latest call decides.
    Once closes reached the ledger, the first call's close made every later
    call read as finished, so the ramp never lifted a rework writer."""
    first_open = _Ev({"model_call": True, "deadline_s": 1800.0, "source": "codewhale_worker"})
    first_close = _Ev({"model_call": False, MODEL_CALL_STATE: CLOSED, "source": "codewhale_worker"})
    second_open = _Ev({"model_call": True, "deadline_s": 1800.0, "source": "codewhale_worker"})
    lift = _Ev({"model_call": True, "deadline_s": 3000.0, "source": "codewhale_worker",
                "cli_wall_extended": True})

    assert budget_inspect._cli_flight([first_open, first_close], {})["cli_in_flight"] is False
    flight = budget_inspect._cli_flight([first_open, first_close, second_open], {})
    assert flight["cli_in_flight"] is True
    # A lift NOTE is the same call -- it never re-opens a closed one.
    assert budget_inspect._cli_flight([first_open, lift, first_close], {})["cli_in_flight"] is False


# -- 2. a granted round brings its wall -------------------------------------


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _writer_gate_failure():
    # A WRITER-gate failure on a brief-defined check (#655): the rule's
    # product-owned REWORK path, no test-file ownership involved.
    return GateResult(
        ok=False,
        gate="writer_behaviour",
        reason="writer_behaviour_failed",
        detail="a capability route reported success over a failed block",
        findings=["cap_a: did not fail closed (F1)"],
    )


def _runner(tmp_path, clock, *, wall, phase, ceiling, writer_seconds):
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=2,
            wall_clock_s=wall,
            phase_wall_clock_s=phase,
            hard_ceiling_s=ceiling,
        ),
        clock=clock,
    )
    calls = {"writer": 0}

    def fake_phase(role, work_list):
        if role is BuildRole.WRITER:
            calls["writer"] += 1
            clock.t += writer_seconds
            if calls["writer"] == 1:
                return _writer_gate_failure()
        else:
            clock.t += 1.0
        return GateResult(ok=True, gate=f"{role.value.lower()}_gate", detail="ok")

    runner._run_phase = fake_phase  # type: ignore[assignment]
    runner._maybe_stage_inspect = lambda *a, **k: None  # type: ignore[assignment]
    return runner, calls


def _grants(ledger):
    return [
        (e.payload or {}).get("wall_grant")
        for e in ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("wall_grant")
    ]


def test_a_rework_round_inside_the_budgets_is_not_stopped_by_the_wall(tmp_path):
    """Wall 100 s, phase allowance 80 s, ceiling 1000 s. The first WRITER pass
    takes 70 s and fails its gate; the rule grants round 1. The rework pass
    takes another 70 s -- the run is now past its original 100 s wall but well
    under the ceiling. Before: FAILED_BUDGET_SPENT at the next phase. After:
    the grant extended the wall and the run carries on, ledgered."""
    clock = _Clock()
    runner, calls = _runner(
        tmp_path, clock, wall=100.0, phase=80.0, ceiling=1000.0, writer_seconds=70.0
    )
    outcome = runner.run()

    assert calls["writer"] == 2, "the rule granted one writer rework"
    assert outcome.outcome is not Outcome.FAILED_BUDGET_SPENT, outcome.detail
    grants = _grants(runner.ledger)
    assert len(grants) == 1
    grant = grants[0]
    assert grant["phases"] == [BuildRole.WRITER.value]
    assert grant["wall_s"] > 100.0 and grant["wall_s"] <= 1000.0
    assert grant["capped"] is False
    # The real run loop went on past the wall to finish the build (the phase
    # stand-in writes no phase events, so the outcome is the evidence).
    assert outcome.outcome is Outcome.SUCCESS, outcome.detail


def test_the_grant_never_hides_the_rework_it_belongs_to(tmp_path):
    """A reopened run resumes from its REWORK, which ledger.reopening_rework()
    reads only while it is the LAST event. The grant is written before the
    REWORK, never after it (CI on #680: a grant NOTE after the REWORK made the
    Store-gate reopen invisible -- 'NoneType' has no attribute 'payload')."""
    clock = _Clock()
    runner, _calls = _runner(
        tmp_path, clock, wall=100.0, phase=80.0, ceiling=1000.0, writer_seconds=70.0
    )
    runner.run()
    events = list(runner.ledger.events())
    rework_at = [i for i, e in enumerate(events) if e.kind is EventKind.REWORK]
    grant_at = [i for i, e in enumerate(events) if (e.payload or {}).get("wall_grant")]
    assert rework_at and grant_at
    assert grant_at[0] == rework_at[0] - 1, "the grant must sit just before its REWORK"


def test_the_grant_never_passes_the_hard_ceiling(tmp_path):
    """Same run, ceiling 120 s: the grant is clamped to the ceiling, the
    ledger says so, and a run that outlives it still stops typed."""
    clock = _Clock()
    runner, _calls = _runner(
        tmp_path, clock, wall=100.0, phase=80.0, ceiling=120.0, writer_seconds=70.0
    )
    outcome = runner.run()

    grant = _grants(runner.ledger)[0]
    assert grant["wall_s"] == 120.0
    assert grant["capped"] is True
    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT
    assert "wall-clock budget" in outcome.detail
