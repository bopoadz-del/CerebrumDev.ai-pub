"""The stopped-build panel tells the truth, once.

The owner's phone, 21 Nov: a writer that ran past its wall read "WRITER was
refused during the build", "GRADER rejected", a raw ``FAILED(...)`` naming the
FIRST timeout of the ledger instead of the one that ended the run, a "failing
check" that was a timeout, and the same machine sentence four times.

The rules these tests pin are general -- no code, product or session is named
by the mechanism:

* the sentence is chosen by the stop's CLASS, read from typed fields;
* everything said about why reads the NEWEST verdict, never the first;
* the grader's state comes only from events the grader wrote;
* a stop that wrote no per-phase verdict is read from the terminal event.

Full-dotted imports so the mutation gate (CI-M) selects these modules.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

import app.factory.build.failure_record as failure_record  # noqa: F401  dotted token for CI-M
import app.factory.build_jobs as build_jobs
from app.factory.build import failure_kinds
from app.factory.build.authority import BuildRole
from app.factory.build.failure_record import (
    CHECK_REFUSED,
    COULD_NOT_RUN,
    OUT_OF_TIME,
    ROUNDS_SPENT,
    STOP_TEXT,
    STOPPED_BY_OWNER,
    UNEXPECTED,
    failure_triple,
    stop_class,
    stop_view,
)
from app.factory.build.grader import CERTIFIED, ERROR, GRADER_GATE, NOT_CERTIFIED
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import REWORK_BUDGET, Outcome
from app.factory.build_jobs import (
    _FAILURE_SUMMARIES,
    _floor_failure,
    _grader_view,
    _phase_trail,
    _redacted,
    build_status,
    failure_records,
)

COLLECTOR, CLONER, WRITER, TESTER, STORE = (
    BuildRole.COLLECTOR,
    BuildRole.CLONER,
    BuildRole.WRITER,
    BuildRole.TESTER,
    BuildRole.STORE_MANAGER,
)
RULE_REWORK = {
    "class": "REWORK",
    "gate": "WRITER",
    "gate_name": "writer_contract",
    "check": "writer_contract",
    "finding": "handler.py:47: the declared code is a literal",
    "round_gate": 1,
    "gate_budget": 1,
    "round_build": 1,
    "build_ceiling": 6,
}


class _Clock:
    """A ledger clock the test advances, so a phase has a real run time."""

    def __init__(self) -> None:
        self.at = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)

    def __call__(self) -> str:
        return self.at.isoformat(timespec="seconds")

    def tick(self, minutes: float) -> None:
        self.at += timedelta(minutes=minutes)


def _new_run(tmp_path, clock=None):
    out = tmp_path / "build"
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl", **({"clock": clock} if clock else {}))
    ledger.start_run(product_id="product", inputs_hash="h")
    return out, ledger


def _pass(ledger, *roles):
    for role in roles:
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")


def _fail_gate(ledger, role, reason, detail="the gate said why"):
    ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
    ledger.append(
        EventKind.GATE_FAILED, role=role, detail=detail, payload={"reason": reason, "location": role.value}
    )


def _writer_times_out(ledger, clock, minutes, kind=failure_kinds.TIMEOUT):
    """What the runner writes when the writer's worker hits its wall: the
    worker's text wrapped under the handler's code, a typed failure_kind, then
    the terminal event and its narrative note."""
    raw = f"codewhale_worker_failed: worker_timed_out: headless job exceeded its {minutes * 60}s wall"
    ledger.append(EventKind.PHASE_STARTED, role=WRITER, detail="WRITER")
    clock.tick(minutes)
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=WRITER,
        detail=raw,
        payload={"reason": "codewhale_worker_failed", "location": "WRITER", "failure_kind": kind},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=WRITER,
        detail=f"WRITER failed: {raw}",
        payload={"outcome": Outcome.FAILED_ROLE_ERROR.value, "findings": []},
    )
    ledger.append(EventKind.NOTE, role=WRITER, detail="failure narrative (ledger)")


# -- the class: typed fields only ---------------------------------------------


@pytest.mark.parametrize(
    "payload, row, expected",
    [
        ({"outcome": "STOPPED_BY_OWNER"}, {"outcome": "aborted", "failure_kind": "timeout"}, STOPPED_BY_OWNER),
        ({"outcome": "FAILED_TIME_BUDGET"}, {"outcome": "failed"}, OUT_OF_TIME),
        ({"outcome": "FAILED_AUTHORITY"}, {"outcome": "aborted"}, COULD_NOT_RUN),
        ({"outcome": "FAILED_BUDGET_SPENT", "decision": {"class": "STOP"}}, {"outcome": "failed"}, ROUNDS_SPENT),
        ({"outcome": "FAILED_BUDGET_SPENT", "decision": {"reason": "x"}}, {"outcome": "failed"}, OUT_OF_TIME),
        ({"outcome": "FAILED_BUDGET_SPENT"}, None, OUT_OF_TIME),
        ({"outcome": "FAILED_ROLE_ERROR"}, {"outcome": "aborted", "failure_kind": "timeout"}, OUT_OF_TIME),
        ({"outcome": "FAILED_ROLE_ERROR"}, {"outcome": "aborted", "failure_kind": "model_call_timeout"}, OUT_OF_TIME),
        ({"outcome": "FAILED_ROLE_ERROR"}, {"outcome": "aborted", "failure_kind": ""}, COULD_NOT_RUN),
        ({"outcome": "FAILED_ROLE_ERROR"}, None, COULD_NOT_RUN),
        ({"outcome": "FAILED_GATE"}, None, CHECK_REFUSED),
        ({"outcome": "FAILED_GATE"}, {"outcome": "aborted"}, CHECK_REFUSED),
        (None, {"outcome": "failed"}, CHECK_REFUSED),
        (None, {"outcome": "aborted"}, COULD_NOT_RUN),
        (None, {"outcome": "aborted", "failure_kind": "timeout"}, OUT_OF_TIME),
        (None, {"outcome": "crashed"}, UNEXPECTED),
        (None, None, UNEXPECTED),
    ],
)
def test_the_stop_class_is_read_from_typed_fields(payload, row, expected):
    assert stop_class(payload, row) == expected


@pytest.mark.parametrize("outcome", [o for o in Outcome if o.name.startswith(("FAILED", "STOPPED"))])
def test_every_way_the_runner_ends_a_run_has_a_class_with_a_sentence(outcome):
    # Exhaustive over the runner's own enum: a new failed outcome can never
    # reach the Floor without a class, and every class has its three texts.
    kind = stop_class({"outcome": outcome.value})
    assert kind != UNEXPECTED
    assert all(STOP_TEXT[kind])


@pytest.mark.parametrize(
    "kind, ran_s, findings, summary",
    [
        (OUT_OF_TIME, 2289, 0, "The time allowance ended before this step finished (it had run for 38 minutes). No check refused the work."),
        (OUT_OF_TIME, 60, 0, "The time allowance ended before this step finished (it had run for 1 minute). No check refused the work."),
        (OUT_OF_TIME, None, 0, "The time allowance ended before this step finished. No check refused the work."),
        (CHECK_REFUSED, None, 3, "A check looked at the work and did not pass it (3 findings)."),
        (CHECK_REFUSED, 900, 1, "A check looked at the work and did not pass it (1 finding)."),
        (ROUNDS_SPENT, None, 0, "A check still did not pass the work, and this build has no fix rounds left."),
        (STOPPED_BY_OWNER, 5000, 4, "You pressed Stop. Nothing failed."),
    ],
)
def test_the_summary_is_built_from_the_class_and_typed_numbers(kind, ran_s, findings, summary):
    view = stop_view(kind, ran_s=ran_s, findings=findings)
    assert view == {"kind": kind, "title": STOP_TEXT[kind][0], "summary": summary, "next": STOP_TEXT[kind][2]}


@pytest.mark.parametrize("kind", ["a_class_nobody_declared", ""])
def test_an_unknown_class_reads_as_unexpected_never_a_crash(kind):
    assert stop_view(kind)["kind"] == UNEXPECTED


@pytest.mark.parametrize("kind", sorted(STOP_TEXT))
def test_no_sentence_names_a_stage_id_a_code_or_a_failing_check(kind):
    text = " ".join(stop_view(kind, ran_s=600, findings=2).values())
    assert not [role.value for role in BuildRole if role.value in text]
    assert "failing check" not in text and "{" not in text
    assert stop_view(kind)["next"].startswith("Continuing ")


# -- FAILED(gate, check, finding): the code is never said twice ---------------


@pytest.mark.parametrize(
    "reason, detail, finding",
    [
        ("codewhale_worker_failed", "codewhale_worker_failed: worker_timed_out: over its wall", "worker_timed_out: over its wall"),
        ("slot_wait_exhausted", "slot_wait_exhausted: slot_wait_exhausted: waited 900s", "waited 900s"),
        ("suite_red", "step 7 failed", "step 7 failed"),
        ("", "plain: prose", "plain: prose"),
    ],
)
def test_the_record_does_not_repeat_the_code_it_is_filed_under(reason, detail, finding):
    triple = failure_triple({}, {"phase": "WRITER", "reason": reason, "detail": detail})
    assert triple == {"gate": "WRITER", "check": reason or "unknown", "finding": finding}


@pytest.mark.parametrize("check, finding", [("suite_green", "test_a failed"), ("metrics_served", "GET /metrics is 404")])
def test_a_rule_stop_is_read_from_its_own_record(check, finding):
    payload = {"decision": {"class": "STOP", "gate": "TESTER", "check": check, "finding": finding}}
    stale = {"phase": "WRITER", "reason": "older_code", "detail": "older prose"}
    assert failure_triple(payload, stale) == {"gate": "TESTER", "check": check, "finding": finding}


# -- the newest verdict, never the first --------------------------------------


@pytest.mark.parametrize("first_min, second_min", [(30, 38), (60, 90)])
def test_a_resumed_run_that_times_out_again_says_so_once(tmp_path, first_min, second_min):
    """The photographed ledger: timeout, Continue, the writer's own gate sends
    the pass back for one fix round, timeout again."""
    clock = _Clock()
    out, ledger = _new_run(tmp_path, clock)
    _pass(ledger, COLLECTOR, CLONER)
    _writer_times_out(ledger, clock, first_min)
    clock.tick(50)
    _fail_gate(ledger, WRITER, "writer_contract_failed")
    ledger.append(EventKind.REWORK, role=WRITER, detail="WRITER round 1/1", payload={"decision": RULE_REWORK})
    _writer_times_out(ledger, clock, second_min, kind=failure_kinds.MODEL_CALL_TIMEOUT)

    status = build_status(out)
    failure, wire = status["failure"], json.dumps(status)

    assert status["state"] == "failed"
    assert (failure["kind"], failure["title"]) == (OUT_OF_TIME, "ran out of time")
    assert failure["summary"] == (
        f"The time allowance ended before this step finished (it had run for {second_min} minutes). "
        "No check refused the work."
    )
    # Nothing about "why" quotes the first timeout, and nothing calls it a refusal.
    assert f"its {second_min * 60}s wall" in status["failed"]["finding"]
    assert f"its {first_min * 60}s wall" not in wire
    assert "was refused" not in wire
    # The code is said once, and a timeout is not a failing check.
    assert "codewhale_worker_failed: codewhale_worker_failed" not in wire
    assert status["failed_label"] == f"FAILED(WRITER, codewhale_worker_failed, {status['failed']['finding']})"
    assert status["next_continue"] == STOP_TEXT[OUT_OF_TIME][2]
    # The writer's own gate is one fix round of history; the grader never spoke.
    assert [d["gate_name"] for d in status["decisions"]] == ["writer_contract"]
    assert status["grader"] is None
    assert failure["ref"] in {record["ref"] for record in failure_records(out)}


@pytest.mark.parametrize("phase", [WRITER, TESTER])
def test_a_resumed_run_shows_no_failure_until_one_stands_again(tmp_path, phase):
    out, ledger = _new_run(tmp_path)
    ledger.append(EventKind.PHASE_STARTED, role=phase, detail=phase.value)
    ledger.append(EventKind.PHASE_ABORTED, role=phase, detail="over its wall", payload={"reason": "worker_failed"})
    ledger.append(EventKind.RUN_FAILED, role=phase, detail="failed", payload={"outcome": "FAILED_ROLE_ERROR"})
    ledger.append(EventKind.NOTE, role=phase, detail="failure narrative (ledger)")
    assert build_status(out)["failure"]["phase"] == phase.value

    ledger.append(EventKind.PHASE_STARTED, role=phase, detail=phase.value)  # Continue
    live = build_status(out)

    assert live["state"] == "building"
    assert live["failure"] is None, "the stop a run resumed from is history, not a rework in progress"
    assert _phase_trail(ledger.events())[1]["phase"] == phase.value  # ...and history keeps it


@pytest.mark.parametrize("ended_first", [False, True])
def test_a_rework_keeps_the_failure_it_is_fixing_standing(tmp_path, ended_first):
    out, ledger = _new_run(tmp_path)
    _fail_gate(ledger, TESTER, "suite_red")
    if ended_first:  # a finished run re-opened by a REWORK (a product-owned gate failure)
        ledger.append(EventKind.RUN_FAILED, role=TESTER, detail="failed", payload={"outcome": "FAILED_GATE"})
    ledger.append(EventKind.REWORK, role=WRITER, detail="round 1", payload={"reopen": ["WRITER", "TESTER"]})
    ledger.append(EventKind.PHASE_STARTED, role=WRITER, detail="WRITER")

    live = build_status(out)

    assert live["state"] == "building"
    assert (live["failure"]["phase"], live["failure"]["reason"]) == ("TESTER", "suite_red")


@pytest.mark.parametrize("last, reason", [(STORE, "acceptance_not_kk"), (TESTER, "product_boot_failed")])
def test_the_failure_shown_is_the_newest_verdict_never_the_first(tmp_path, last, reason):
    out, ledger = _new_run(tmp_path)
    _pass(ledger, COLLECTOR, CLONER)
    _fail_gate(ledger, WRITER, "writer_contract_failed")  # reworked, then it passes
    ledger.append(EventKind.REWORK, role=WRITER, detail="round 1", payload={"decision": RULE_REWORK})
    _pass(ledger, *[role for role in (WRITER, TESTER) if role is not last])
    _fail_gate(ledger, last, reason)
    ledger.append(
        EventKind.RUN_FAILED,
        role=last,
        detail="failed",
        payload={"outcome": "FAILED_GATE", "findings": ["step 7 failed"]},
    )

    status = build_status(out)

    assert (status["failure"]["phase"], status["failure"]["reason"]) == (last.value, reason)
    assert status["failed"] == {"gate": last.value, "check": reason, "finding": "step 7 failed"}
    assert status["failure"]["summary"] == "A check looked at the work and did not pass it (1 finding)."
    assert _phase_trail(ledger.events())[1]["phase"] == "WRITER"


# -- stops that write no per-phase verdict ------------------------------------


@pytest.mark.parametrize(
    "outcome, role, record, kind",
    [
        (Outcome.FAILED_TIME_BUDGET, TESTER, {"reason": "time_budget_exceeded", "wall_clock_s": 3600.0}, OUT_OF_TIME),
        (Outcome.STOPPED_BY_OWNER, None, {"reason": "stopped_by_owner", "by": "owner"}, STOPPED_BY_OWNER),
        (Outcome.FAILED_BUDGET_SPENT, STORE, None, OUT_OF_TIME),
        (Outcome.FAILED_GATE, None, None, CHECK_REFUSED),
    ],
)
def test_a_stop_with_no_phase_verdict_is_read_from_the_terminal_event(tmp_path, outcome, role, record, kind):
    out, ledger = _new_run(tmp_path)
    _pass(ledger, COLLECTOR, CLONER, WRITER)
    payload = {"outcome": outcome.value, "findings": [], **({"decision": record} if record else {})}
    ledger.append(EventKind.RUN_FAILED, role=role, detail="the build stopped here", payload=payload)

    status = build_status(out)
    failure = status["failure"]

    # Where it stopped is a location; no phase is blamed for a verdict nobody gave.
    assert (failure["kind"], failure["location"], failure["phase"]) == (kind, role.value if role else "", "")
    assert status["failed"]["gate"] == (role.value if role else "unknown")
    assert set(status["level_grade"]["three_gate"].values()) == {"UNKNOWN"}
    assert failure["code"] == outcome.value.lower()  # never "build_thread_crashed"
    assert status["decisions"] == []  # those records are not rule decisions: no blank row
    assert status["grader"] is None
    assert status["next_continue"] == STOP_TEXT[kind][2]
    assert failure["ref"] in {r["ref"] for r in failure_records(out)}


@pytest.mark.parametrize("handoff_recorded", [True, False])
def test_a_designed_handoff_is_a_wait_for_the_store_gate_not_a_stop(tmp_path, handoff_recorded):
    out, ledger = _new_run(tmp_path)
    _pass(ledger, COLLECTOR, CLONER, WRITER, TESTER)
    _fail_gate(ledger, STORE, "docker_unavailable")
    if handoff_recorded:
        ledger.append(EventKind.NOTE, role=STORE, detail="handed off", payload={"handoff": "HANDOFF_TO_N3"})
    ledger.append(EventKind.RUN_FAILED, role=STORE, detail="handed off", payload={"outcome": "HANDOFF_TO_N3"})

    status = build_status(out)

    assert (status["state"], status["n3_waiting"]) == ("building", True)
    # The cycle's terminal event is never read as a stop row while it waits.
    assert (status["failure"] is None) == handoff_recorded


@pytest.mark.parametrize(
    "outcome, kind", [(Outcome.FAILED_TIME_BUDGET, OUT_OF_TIME), (Outcome.STOPPED_BY_OWNER, STOPPED_BY_OWNER)]
)
def test_a_typed_stop_at_another_phase_does_not_wear_an_older_failure(tmp_path, outcome, kind):
    out, ledger = _new_run(tmp_path)
    _fail_gate(ledger, TESTER, "suite_red")
    ledger.append(EventKind.REWORK, role=WRITER, detail="round 1", payload={"decision": RULE_REWORK})
    ledger.append(EventKind.RUN_FAILED, role=WRITER, detail="stopped", payload={"outcome": outcome.value})

    failure = build_status(out)["failure"]

    assert (failure["location"], failure["kind"], failure["reason"]) == ("WRITER", kind, outcome.value.lower())


@pytest.mark.parametrize("phase", [WRITER, TESTER])
def test_a_typed_stop_inside_a_running_phase_names_that_phase(tmp_path, phase):
    out, ledger = _new_run(tmp_path)
    ledger.append(EventKind.PHASE_STARTED, role=phase, detail=phase.value)
    ledger.append(EventKind.RUN_FAILED, role=phase, detail="stopped", payload={"outcome": "FAILED_GATE"})

    failure = build_status(out)["failure"]

    assert (failure["phase"], failure["location"], failure["kind"]) == (phase.value, phase.value, CHECK_REFUSED)


@pytest.mark.parametrize("phase", [WRITER, TESTER])
def test_a_crashed_thread_names_the_phase_it_died_in_over_an_older_failure(tmp_path, phase):
    out, ledger = _new_run(tmp_path)
    _fail_gate(ledger, STORE, "acceptance_not_kk")
    ledger.append(EventKind.REWORK, role=WRITER, detail="round 1", payload={"decision": RULE_REWORK})
    ledger.append(EventKind.PHASE_STARTED, role=phase, detail=phase.value)
    ledger.append(EventKind.RUN_FAILED, detail="build thread crashed: KeyError", payload={"reason": "build_thread_crashed"})

    failure = build_status(out)["failure"]

    assert (failure["phase"], failure["kind"], failure["title"]) == (phase.value, UNEXPECTED, "stopped unexpectedly")
    assert failure["summary"] == _FAILURE_SUMMARIES["build_thread_crashed"]


@pytest.mark.parametrize(
    "outcome, kind", [(Outcome.STOPPED_BY_OWNER, STOPPED_BY_OWNER), (Outcome.FAILED_TIME_BUDGET, OUT_OF_TIME)]
)
def test_the_runs_outcome_is_never_described_by_an_older_events_code(outcome, kind):
    row = {"phase": "WRITER", "location": "WRITER", "outcome": "failed", "reason": "writer_no_output", "timestamp": "t"}
    said = _floor_failure(row, {"outcome": outcome.value})
    assert said["kind"] == kind
    assert said["summary"] == STOP_TEXT[kind][1].format(ran="", items="")


@pytest.mark.parametrize("code", ["slot_wait_exhausted", "suite_red"])
def test_an_unmapped_code_is_logged_once_per_failure_with_the_code_in_the_message(code, caplog, monkeypatch):
    monkeypatch.setattr(build_jobs, "_LOGGED_FAILURE_REFS", set())
    row = {"phase": "WRITER", "location": "WRITER", "outcome": "aborted", "reason": code, "timestamp": "t"}
    with caplog.at_level(logging.INFO, logger="cerebrumdev.factory.build_jobs"):
        _floor_failure(dict(row))
        _floor_failure(dict(row))  # the next status read of the same failure
    lines = [r.getMessage() for r in caplog.records if "no sentence of its own" in r.getMessage()]
    assert len(lines) == 1
    assert code in lines[0]  # in the MESSAGE: the formatter prints no `extra`


# -- the record: every ref resolves, keys never leave -------------------------


@pytest.mark.parametrize("first, second", [("suite is red: step 3", "suite is red: step 9"), ("boot failed", "boot failed again")])
def test_every_ref_the_floor_showed_still_resolves(tmp_path, first, second):
    clock = _Clock()
    out, ledger = _new_run(tmp_path, clock)
    _fail_gate(ledger, TESTER, "suite_red", first)
    first_ref = build_status(out)["failure"]["ref"]
    ledger.append(EventKind.REWORK, role=WRITER, detail="round 1", payload={"decision": RULE_REWORK})
    clock.tick(5)
    _fail_gate(ledger, TESTER, "suite_red", second)
    second_ref = build_status(out)["failure"]["ref"]

    filed = {record["ref"]: record["detail"] for record in failure_records(out)}

    assert first_ref != second_ref
    assert filed == {first_ref: first, second_ref: second}


@pytest.mark.parametrize("detail, filed", [("", 0), ("the gate said why", 1)])
def test_a_failure_with_no_detail_files_no_record(tmp_path, detail, filed):
    out, ledger = _new_run(tmp_path)
    _fail_gate(ledger, WRITER, "writer_no_output", detail)
    assert len(failure_records(out)) == filed


@pytest.mark.parametrize("secret", ["DEEPSEEK_API_KEY=planted-value-one", "OPENROUTER_API_KEY=planted-value-two"])
def test_a_key_in_the_stop_prose_never_reaches_the_wire(tmp_path, secret):
    """The control in the failing direction: a STOPPED build (the earlier
    tripwire test never had a terminal event). The key is planted in the
    terminal detail, a finding and the rule's stop record."""
    name, value = secret.split("=")
    out, ledger = _new_run(tmp_path)
    _fail_gate(ledger, WRITER, "writer_contract_failed", f"the gate quoted {secret}")
    stop = {**RULE_REWORK, "class": "STOP", "finding": f"finding quoting {secret}"}
    ledger.append(
        EventKind.RUN_FAILED,
        role=WRITER,
        detail=f"WRITER failed: {secret}",
        payload={"outcome": "FAILED_GATE", "findings": [f"finding quoting {secret}"], "decision": stop},
    )

    status = build_status(out)

    assert value not in json.dumps(status)
    assert status["detail"] == f"WRITER failed: {name}=[redacted]"
    assert status["failed"]["finding"] == f"finding quoting {name}=[redacted]"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("plain words", "plain words"),
        ({"a": ["x DEEPSEEK_API_KEY=planted-value", 3, None]}, {"a": ["x DEEPSEEK_API_KEY=[redacted]", 3, None]}),
        (7, 7),
    ],
)
def test_redaction_reaches_every_string_however_deep(value, expected):
    assert _redacted(value) == expected


@pytest.mark.parametrize("name", ["no_ledger_a", "no_ledger_b"])
def test_no_ledger_is_unknown_not_a_stop(tmp_path, name):
    assert build_status(tmp_path / name)["state"] == "unknown"


# -- the grader chip speaks only for the grader -------------------------------


def _grader(view_state, tier, round_no=0):
    return {"state": view_state, "tier": tier, "round": round_no, "budget": REWORK_BUDGET}


_GRADER_STEPS = {
    "writer_gate_rework": (EventKind.REWORK, {"gate": "writer_contract", "decision": RULE_REWORK}),
    "writer_gate_failed": (EventKind.GATE_FAILED, {"gate": "writer_contract", "reason": "writer_contract_failed"}),
    "timeout": (EventKind.PHASE_ABORTED, {"reason": "codewhale_worker_failed", "failure_kind": "timeout"}),
    "certified": (EventKind.NOTE, {"grader_gate": GRADER_GATE, "grader_tier": CERTIFIED}),
    "not_certified": (EventKind.NOTE, {"grader_gate": GRADER_GATE, "grader_tier": NOT_CERTIFIED}),
    "error": (EventKind.NOTE, {"grader_gate": GRADER_GATE, "grader_tier": ERROR}),
    "handback": (EventKind.REWORK, {"gate": GRADER_GATE, "source": "GRADER", "round": 1, "grader_tier": NOT_CERTIFIED}),
    "stop": (EventKind.GATE_FAILED, {"gate": GRADER_GATE, "reason": "grader_not_certified", "grader_tier": NOT_CERTIFIED}),
    "stop_error": (EventKind.GATE_FAILED, {"gate": GRADER_GATE, "reason": "grader_error", "grader_tier": ERROR}),
    "run_failed": (EventKind.RUN_FAILED, {"outcome": "FAILED_GATE", "decision": {"reason": "stopped_by_owner"}}),
    "resume": (EventKind.PHASE_STARTED, {}),
}


@pytest.mark.parametrize(
    "steps, expected",
    [
        # Nothing the grader wrote: another gate's decision, a timeout, a stop.
        ((), None),
        (("writer_gate_failed", "writer_gate_rework", "resume", "timeout", "run_failed"), None),
        (("certified",), _grader("certified", CERTIFIED)),
        (("not_certified",), _grader("refused", NOT_CERTIFIED)),
        (("not_certified", "handback"), _grader("sent_back", NOT_CERTIFIED, 1)),
        (("not_certified", "handback", "resume", "certified"), _grader("certified", CERTIFIED, 1)),
        (("not_certified", "handback", "not_certified", "stop", "run_failed"), _grader("stopped", NOT_CERTIFIED, 1)),
        (("error", "stop_error", "run_failed"), _grader("stopped", ERROR)),
        # Continue after a grader stop: it has not judged the resumed attempt.
        (("not_certified", "stop", "run_failed", "resume"), None),
        (("not_certified", "stop", "run_failed", "resume", "writer_gate_failed"), None),
    ],
)
def test_the_grader_view_reads_only_what_the_grader_wrote(tmp_path, steps, expected):
    out, ledger = _new_run(tmp_path)
    for step in steps:
        kind, payload = _GRADER_STEPS[step]
        ledger.append(kind, role=WRITER, detail=step, payload=payload)

    assert _grader_view(ledger.events()) == expected
    assert build_status(out)["grader"] == expected
