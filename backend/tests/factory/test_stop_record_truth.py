"""The stop record says where the run ended, why, and what is kept.

Four ledger shapes from a real runner each gave the page a wrong record:

* a fix round sent a later gate's work back to the WRITER, which then ran
  out of time: the record named the earlier gate's step, not the WRITER;
* a stage time check stopped the run (FAILED_BUDGET_SPENT, the outcome a
  spent rework budget also has): the record said a check still failed after
  the fix rounds, with zero rounds spent;
* the grader sent the work back: its handback is a REWORK event with no rule
  decision, so the record counted zero fix rounds;
* a fix round re-entered a step that had passed once: the record counted
  that step as done while saying the build stopped in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.factory.build.failure_record as fr
from app.factory.build.authority import BuildRole
from app.factory.build.grader import GRADER_GATE
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import STAGE_TIME_CHECK_STOP
from app.factory.build_jobs import build_status
from app.factory.floor_actions import allowed_build_actions


def _ledger(out: Path) -> BuildLedger:
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER, BuildRole.WRITER):
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    return ledger


def _send_back_to_writer(ledger: BuildLedger, gate_role: BuildRole) -> None:
    """A later gate refused; the rule sent the work back to the WRITER."""
    ledger.append(EventKind.PHASE_STARTED, role=gate_role)
    ledger.append(
        EventKind.GATE_FAILED,
        role=gate_role,
        detail="a check did not pass",
        payload={
            "gate": gate_role.value,
            "reason": "suite_failed",
            "location": gate_role.value,
            "decision": {"gate": gate_role.value, "class": "REWORK", "check": "c", "finding": "f"},
        },
    )
    ledger.append(EventKind.REWORK, role=BuildRole.WRITER, payload={"gate": gate_role.value})
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)


@pytest.mark.parametrize("gate_role", [BuildRole.TESTER, BuildRole.STORE_MANAGER])
def test_a_writer_fix_round_that_ran_out_of_time_stops_at_the_writer(tmp_path, gate_role):
    out = tmp_path / "p"
    ledger = _ledger(out)
    _send_back_to_writer(ledger, gate_role)
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="time_budget_exceeded: wall-clock budget of 3600s spent before WRITER completed",
        payload={"outcome": "FAILED_TIME_BUDGET", "decision": {"reason": "time_budget_exceeded"}},
    )

    stop = build_status(out)["stop"]

    assert stop["kind"] == fr.STOP_TIME_RAN_OUT
    assert stop["step"] == "WRITER"
    assert stop["step_index"] == 3
    # Steps 1 and 2 are kept; the WRITER's earlier pass was sent back.
    assert stop["steps_done"] == 2
    assert stop["fix_rounds"] == 1


@pytest.mark.parametrize(
    "decision, kind",
    [
        # The stage time check's own typed reason: time, not a check.
        ({"reason": STAGE_TIME_CHECK_STOP}, fr.STOP_TIME_RAN_OUT),
        # The same outcome with no typed reason is a spent rework budget.
        (None, fr.STOP_CHECK_FAILED),
    ],
)
def test_a_stage_time_check_stop_is_time_not_a_failed_check(tmp_path, decision, kind):
    out = tmp_path / "p"
    ledger = _ledger(out)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER)
    payload = {"outcome": "FAILED_BUDGET_SPENT"}
    if decision is not None:
        payload["decision"] = decision
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail="wall-clock budget of 1800s spent before TESTER completed; inspect stage_1: hard-stop",
        payload=payload,
    )

    stop = build_status(out)["stop"]

    assert stop["kind"] == kind
    assert stop["step"] == "TESTER"
    assert stop["fix_rounds"] == 0
    if decision is not None:
        assert stop["plain"] == fr.STOP_ENTRIES[STAGE_TIME_CHECK_STOP][1]
        assert "fix round" not in stop["plain"]


@pytest.mark.parametrize("handbacks", [1, 2])
def test_each_grader_handback_is_a_fix_round(tmp_path, handbacks):
    out = tmp_path / "p"
    ledger = _ledger(out)
    for n in range(handbacks):
        ledger.append(
            EventKind.REWORK,
            role=BuildRole.WRITER,
            detail="GRADER: NOT_CERTIFIED -- writer handback",
            payload={"gate": GRADER_GATE, "source": "GRADER", "round": n + 1},
        )
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.WRITER,
        detail="GRADER: NOT_CERTIFIED",
        payload={"gate": GRADER_GATE, "reason": "grader_not_certified"},
    )
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, payload={"outcome": "FAILED_GATE"})

    stop = build_status(out)["stop"]

    assert stop["kind"] == fr.STOP_CHECK_FAILED
    assert stop["fix_rounds"] == handbacks


@pytest.mark.parametrize("passed_after", [(BuildRole.TESTER,), (BuildRole.TESTER, BuildRole.STORE_MANAGER)])
def test_the_step_it_stopped_in_is_never_counted_done(tmp_path, passed_after):
    """Later steps passed, then a fix round sent the work back to the WRITER,
    which could not run: steps 1 and 2 are kept, the WRITER is not done."""
    out = tmp_path / "p"
    ledger = _ledger(out)
    for role in passed_after:
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.REWORK, role=BuildRole.WRITER, payload={"gate": "pilot"})
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=BuildRole.WRITER,
        detail="pilot_rework_no_coder_key: the pass could not run",
        payload={"reason": "pilot_rework_no_coder_key", "location": "WRITER"},
    )
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, payload={"outcome": "FAILED_ROLE_ERROR"})

    status = build_status(out)
    stop = status["stop"]

    assert stop["step_index"] == 3
    assert stop["steps_done"] == 2
    assert stop["steps_done"] <= int(status.get("phases_done") or 0)


@pytest.mark.parametrize("max_rework", [1, 2])
def test_the_runner_records_its_stage_time_check_stop_as_time(tmp_path, max_rework):
    """A real run halted by the stage time check reads as time, not a check."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.budget_inspect import STAGE_1_S
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.roles_models import RoleResult
    from app.factory.build.runner import BuildBudget, RoleRunner

    now = {"t": 0.0}

    def templated(ctx):
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
        return RoleResult(ok=True, detail="templated")

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = templated
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(max_rework=max_rework, wall_clock_s=STAGE_1_S, phase_wall_clock_s=STAGE_1_S),
        clock=lambda: now["t"],
        auto_pilot=True,
        brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}',
    )
    outcome = runner.run()

    assert outcome.outcome.value == "FAILED_BUDGET_SPENT"
    terminal = runner.ledger.terminal_event()
    assert (terminal.payload or {})["decision"]["reason"] == STAGE_TIME_CHECK_STOP
    stop = build_status(tmp_path / "build")["stop"]
    assert stop["kind"] == fr.STOP_TIME_RAN_OUT
    assert stop["fix_rounds"] == 0


@pytest.mark.parametrize("max_rework", [1, 2])
def test_a_stage_time_check_stop_between_steps_reads_as_time(tmp_path, monkeypatch, max_rework):
    """The stage time check that halts the run at the top of the next step,
    before it starts, records its typed reason too: time, never "a check
    still did not pass after the fix rounds" with zero rounds spent.

    The inspect's own decision is budget_inspect's concern; here it decides
    hard_stop once the clock is past the mark, and the CLONER ends past it
    with no progress line after, so the halt lands between steps."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.budget_inspect import STAGE_1_S
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.runner import BuildBudget, RoleRunner

    now = {"t": 0.0}

    def inspect_halts_past_the_mark(self):
        if self.clock() >= STAGE_1_S and not self._stage_halt:
            self._stage_halt = {"decision": "hard_stop", "reason": "stage stop after inspect"}
        return self._stage_halt

    monkeypatch.setattr(RoleRunner, "_maybe_stage_inspect", inspect_halts_past_the_mark)

    real_cloner = ROLE_IMPLEMENTATIONS[BuildRole.CLONER]

    def cloner_ends_past_the_mark(ctx):
        result = real_cloner(ctx)
        now["t"] = STAGE_1_S + 5
        return result

    root = Path(__file__).resolve().parents[3]
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.CLONER] = cloner_ends_past_the_mark
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(max_rework=max_rework, wall_clock_s=4 * STAGE_1_S, phase_wall_clock_s=4 * STAGE_1_S),
        clock=lambda: now["t"],
        auto_pilot=True,
        brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}',
    )
    outcome = runner.run()

    assert outcome.outcome.value == "FAILED_BUDGET_SPENT"
    started = [e.role for e in runner.ledger.events() if e.kind == EventKind.PHASE_STARTED]
    assert BuildRole.CLONER in started
    assert BuildRole.WRITER not in started
    terminal = runner.ledger.terminal_event()
    assert (terminal.payload or {})["decision"]["reason"] == STAGE_TIME_CHECK_STOP
    stop = build_status(tmp_path / "build")["stop"]
    assert stop["kind"] == fr.STOP_TIME_RAN_OUT
    assert stop["fix_rounds"] == 0


@pytest.mark.parametrize("kind", ["user_stopped", "time_ran_out"])
def test_a_stopped_build_offers_change_answers_which_the_server_accepts(kind):
    """continue_with_intake answers every failed run, the owner's stop too."""
    actions = allowed_build_actions({"state": "failed", "stop": {"kind": kind}})

    assert "change_answers" in actions
    assert actions[0] == "try_again"


@pytest.mark.parametrize(
    "reason, check_plain",
    [
        # A check with a sentence of its own: the server sends it.
        ("writer_behaviour_failed", fr.STOP_ENTRIES["writer_behaviour_failed"][1]),
        # A gate refusal with no entry: no sentence of its own.
        ("writer_contract", None),
    ],
)
def test_a_check_sends_its_own_sentence_only_when_it_has_one(tmp_path, reason, check_plain):
    out = tmp_path / "p"
    ledger = _ledger(out)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER)
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.TESTER,
        detail=f"{reason}: a check did not pass",
        payload={"gate": "TESTER", "reason": reason, "location": "TESTER"},
    )
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.TESTER, payload={"outcome": "FAILED_GATE"})

    stop = build_status(out)["stop"]

    assert stop["kind"] == fr.STOP_CHECK_FAILED
    assert stop["check_plain"] == check_plain
    assert stop["restarted"] is False


@pytest.mark.parametrize(
    "honesty, restarted",
    [
        ("FACTORY_CODE_CLI_ORPHANED", True),
        ("BUILD_THREAD_ORPHANED", True),
        ("BUILD_THREAD_CRASHED", False),
    ],
)
def test_a_restart_is_told_apart_from_a_crash(tmp_path, honesty, restarted):
    from app.factory.build_jobs import stop_record

    out = tmp_path / "p"
    _ledger(out)

    stop = stop_record({"state": "failed", "honesty": honesty, "phases_done": 2}, out)

    assert stop["kind"] == fr.STOP_SERVICE_INTERRUPTED
    assert stop["restarted"] is restarted
    assert stop["check_plain"] is None


@pytest.mark.parametrize("code", sorted(fr.GENERIC_CHECK_CODES))
def test_a_generic_check_code_sends_no_sentence_of_its_own(code):
    """A spent rework budget or a bare gate refusal is the generic "a check
    did not pass": the page already says that, so no second sentence is sent,
    whatever plain text the code carries."""
    own = "The rework rounds for this build are used up."

    assert fr.check_plain(fr.STOP_CHECK_FAILED, code, own) is None
    assert fr.check_plain(fr.STOP_CHECK_FAILED, code + "_X", own) == own


@pytest.mark.parametrize("gate_role", [BuildRole.TESTER, BuildRole.STORE_MANAGER])
def test_an_owner_stop_between_steps_after_a_send_back_stops_at_the_writer(tmp_path, gate_role):
    """The owner stops after a later gate sent the work back, before the
    WRITER restarts: the run's terminal event names no step (the stop landed
    between steps), so the step is where the fix round sent the work, not the
    refused gate, and the WRITER's thrown-away pass is not counted kept."""
    from app.factory.build.runner import STOPPED_BY_OWNER_REASON

    out = tmp_path / "p"
    ledger = _ledger(out)
    ledger.append(EventKind.PHASE_STARTED, role=gate_role)
    ledger.append(
        EventKind.GATE_FAILED,
        role=gate_role,
        detail="a check did not pass",
        payload={"gate": gate_role.value, "reason": "suite_failed", "location": gate_role.value},
    )
    ledger.append(
        EventKind.REWORK,
        role=BuildRole.WRITER,
        payload={"gate": gate_role.value, "decision": {"gate": gate_role.value, "class": "REWORK"}},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        detail="stopped by the owner during the build",
        payload={"outcome": "STOPPED_BY_OWNER", "decision": {"reason": STOPPED_BY_OWNER_REASON}},
    )

    stop = build_status(out)["stop"]

    assert stop["kind"] == fr.STOP_USER_STOPPED
    assert stop["step"] == "WRITER"
    assert stop["step_index"] == 3
    assert stop["steps_done"] == 2
    assert stop["fix_rounds"] == 1


def _passes(ledger: BuildLedger, *roles: BuildRole) -> None:
    for role in roles:
        ledger.append(EventKind.PHASE_STARTED, role=role)
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})


def _owner_stop(ledger: BuildLedger) -> None:
    """The owner's stop caught between steps: the terminal event names none."""
    from app.factory.build.runner import STOPPED_BY_OWNER_REASON

    ledger.append(
        EventKind.RUN_FAILED,
        detail="stopped by the owner during the build",
        payload={"outcome": "STOPPED_BY_OWNER", "decision": {"reason": STOPPED_BY_OWNER_REASON}},
    )


@pytest.mark.parametrize(
    "gate_role, passed_after, step, step_index, steps_done",
    [
        (BuildRole.TESTER, (BuildRole.WRITER,), "TESTER", 4, 3),
        (BuildRole.STORE_MANAGER, (BuildRole.WRITER,), "TESTER", 4, 3),
        (BuildRole.STORE_MANAGER, (BuildRole.WRITER, BuildRole.TESTER), "STORE_MANAGER", 5, 4),
    ],
)
def test_phases_passed_after_a_send_back_move_the_stop_step_on(
    tmp_path, gate_role, passed_after, step, step_index, steps_done
):
    """A send-back, then the WRITER (and maybe more) passed again, then the
    owner's stop between steps: the work stood at the next phase, not at the
    WRITER the fix round named, and the phases that passed are kept."""
    out = tmp_path / "p"
    ledger = _ledger(out)
    _send_back_to_writer(ledger, gate_role)
    _passes(ledger, *passed_after)
    _owner_stop(ledger)

    stop = build_status(out)["stop"]

    assert stop["kind"] == fr.STOP_USER_STOPPED
    assert (stop["step"], stop["step_index"], stop["steps_done"]) == (step, step_index, steps_done)


@pytest.mark.parametrize("passed_after", [(), (BuildRole.TESTER,)])
def test_a_later_grader_handback_is_read_from_the_latest_send_back(tmp_path, passed_after):
    """Two send-backs after the last failure (a gate's, then the grader's
    after every phase passed): the latest one names the WRITER, and only the
    phases passed after it move the step on."""
    out = tmp_path / "p"
    ledger = _ledger(out)
    _send_back_to_writer(ledger, BuildRole.TESTER)
    _passes(ledger, BuildRole.WRITER, BuildRole.TESTER, BuildRole.STORE_MANAGER)
    ledger.append(
        EventKind.REWORK, role=BuildRole.WRITER, detail="grader", payload={"gate": GRADER_GATE}
    )
    _passes(ledger, BuildRole.WRITER, *passed_after)
    _owner_stop(ledger)

    stop = build_status(out)["stop"]

    expected = "STORE_MANAGER" if passed_after else "TESTER"
    assert stop["step"] == expected
    assert stop["steps_done"] == (4 if passed_after else 3)


class _Ev:
    def __init__(self, kind: str, role: str = "") -> None:
        self.kind, self.role = kind, role


@pytest.mark.parametrize("failed_role", ["TESTER", "STORE_MANAGER"])
def test_where_work_stood_past_the_last_phase_is_no_step(failed_role):
    """Every phase passed after the failure's send-back: there is no step
    left, so none is named. With nothing after the failure it is the failed
    step itself, and an event with no role moves nothing."""
    from app.factory.build_jobs import _where_work_stood

    phases = ["COLLECTOR", "CLONER", "WRITER", "TESTER", "STORE_MANAGER"]
    last = _Ev("GATE_FAILED", failed_role)
    tail = [_Ev("REWORK", "WRITER")] + [_Ev("GATE_PASSED", r) for r in phases[2:]]
    earlier = [_Ev("GATE_PASSED", "COLLECTOR")]

    assert _where_work_stood(earlier + [last] + tail, last, phases) == ""
    assert _where_work_stood(earlier + [last, _Ev("NOTE")], last, phases) == failed_role
    assert _where_work_stood(earlier + [last, _Ev("GATE_PASSED", "OTHER")], last, phases) == failed_role


@pytest.mark.parametrize("failed_role", ["TESTER", "STORE_MANAGER"])
def test_a_fix_round_with_no_role_moves_no_step(failed_role):
    """The ledger's role is optional. A fix round or a pass recorded with no
    role names no step: the work stays where it stood, and a later event that
    does carry a role still moves it."""
    from app.factory.build_jobs import _where_work_stood

    phases = ["COLLECTOR", "CLONER", "WRITER", "TESTER", "STORE_MANAGER"]
    last = _Ev("GATE_FAILED", failed_role)

    assert _where_work_stood([last, _Ev("REWORK")], last, phases) == failed_role
    assert _where_work_stood([last, _Ev("GATE_PASSED")], last, phases) == failed_role
    assert _where_work_stood([last, _Ev("REWORK"), _Ev("REWORK", "WRITER")], last, phases) == "WRITER"


@pytest.mark.parametrize(
    "said, decided",
    [("worker_stalled", "writer_no_output"), ("writer_no_output", "worker_stalled")],
)
def test_the_runners_typed_reason_is_read_before_the_terminal_text(tmp_path, said, decided):
    """The terminal event's own text names one stop code and the runner's
    typed decision another: the typed decision is the one the record keeps."""
    out = tmp_path / "p"
    ledger = _ledger(out)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER)
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail=f"{said}: the step ended",
        payload={"outcome": "FAILED_ROLE_ERROR", "reason": said, "decision": {"reason": decided}},
    )

    stop = build_status(out)["stop"]

    assert stop["technical"]["code"] == decided
    assert stop["plain"] == fr.STOP_ENTRIES[decided][1]


@pytest.mark.parametrize("handbacks", [1, 2])
def test_a_grader_send_back_with_a_rule_decision_counts_once(tmp_path, handbacks):
    """A grader-gate send-back that also carries a rule decision is counted by
    the rule's decisions; it is not counted a second time as a handback."""
    out = tmp_path / "p"
    ledger = _ledger(out)
    for n in range(handbacks):
        ledger.append(
            EventKind.REWORK,
            role=BuildRole.WRITER,
            detail="GRADER: NOT_CERTIFIED -- writer handback",
            payload={
                "gate": GRADER_GATE,
                "round": n + 1,
                "decision": {"gate": GRADER_GATE, "class": "REWORK"},
            },
        )
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, payload={"outcome": "FAILED_GATE"})

    stop = build_status(out)["stop"]

    assert stop["fix_rounds"] == handbacks
