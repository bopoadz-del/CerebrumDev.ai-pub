"""The foreman is ON unless the setting turns it off; a gate gets three rework
rounds, never a fourth; the build ceiling of six still ends the build.

Fleet manager decision (26 Nov 23:32Z): ``FACTORY_FOREMAN`` defaults to on
(``off`` and ``shadow`` still work); REWORK_BUDGET goes from 1 to 3 per gate;
REWORK_CEILING stays 6. Every test here runs with the setting UNSET, so it
proves the default, and with a scripted model (no paid call).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import architect
from app.factory.build import brief_gates
from app.factory.build import failure_record as fr
from app.factory.build import foreman as fm
from app.factory.build import runner as runner_mod
from app.factory.build.acceptance_floor import PRODUCT, enforced_ids, owner_of
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.findings import Finding
from app.factory.build.gates import GateResult
from app.factory.build.grader import GRADER_GATE
from app.factory.build.ledger import EventKind
from app.factory.build.n3_store_gate import store_gate_verdict
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import (
    DECISION_REWORK,
    DECISION_STOP,
    REWORK_BUDGET,
    REWORK_CEILING,
    BuildBudget,
    Outcome,
    RoleRunner,
)
from app.factory.build_jobs import _max_rework, build_status

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
BRIEF = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'
W, T, S = BuildRole.WRITER, BuildRole.TESTER, BuildRole.STORE_MANAGER


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)
    monkeypatch.delenv("FACTORY_BUILD_MAX_REWORK", raising=False)
    monkeypatch.delenv(fm.FLAG_ENV, raising=False)


class _Llm:
    """A scripted model: answers in order, then repeats the last answer."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _reply(cap, file="app/models.py", what="declare every field the handler reads"):
    return {
        "mechanism": "the handler requires a field its model never declares",
        "affected_capability_ids": [cap],
        "instructions": [
            {
                "file": file,
                "what_to_build": what,
                "why": "a payload built from the model's own schema must be accepted",
                "capability_id": cap,
            }
        ],
        "confidence": 0.6,
        "recommend": "continue",
        "stop_reason": None,
        "proposed_gates": [],
    }


def _tester_fail(cap, shape):
    finding = Finding(
        f"FAILED tests/test_routes.py::test_{shape} - refused",
        gate="TESTER",
        check_id=brief_gates.SUITE_CHECK,
        capability_id=cap,
        file="tests/test_routes.py",
        finding_shape=shape,
    )
    return GateResult(
        ok=False,
        gate="tester_gate",
        reason="scripted",
        detail="suite red",
        findings=[finding],
        payload={"check": brief_gates.SUITE_CHECK, "finding_checks": [brief_gates.SUITE_CHECK], "finding_shape": shape},
    )


def _fail(role, check, shape):
    return GateResult(
        ok=False,
        gate=f"{role.value.lower()}_gate",
        reason="scripted",
        detail=f"{check} failed",
        findings=[f"{check}: scripted finding ({shape})"],
        payload={"check": check, "finding_checks": [check], "finding_shape": shape},
    )


def _run(tmp_path, monkeypatch, script, *, llm=None, budget=None):
    """Every phase is a stub; role R's gate answers ``script[R]`` in order,
    then passes. ``budget`` None = the production default (BuildBudget())."""
    if llm is not None:
        monkeypatch.setattr(architect, "_default_llm", lambda: llm)
    queues = {role: list(script.get(role, ())) for role in BUILD_PHASES}
    calls = []

    def gate_for(role):
        role = BuildRole(role)

        def gate(ctx):
            if queues[role]:
                return queues[role].pop(0)
            return GateResult(ok=True, gate=f"{role.value.lower()}_gate", detail="pass")

        return gate

    def stub(role):
        def run(ctx):
            if role is W:
                calls.append(tuple(ctx.work_list))
            return RoleResult(ok=True, detail="stub")

        return run

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        roles={role: stub(role) for role in BUILD_PHASES},
        budget=budget or BuildBudget(),
        brief=BRIEF,
    )
    return runner, runner.run(), calls


def _decisions(runner, kind):
    out = []
    for event in runner.ledger.events():
        rec = (event.payload or {}).get("decision")
        if isinstance(rec, dict) and rec["class"] == kind:
            out.append(rec)
    return out


def _caps():
    return [c.id for c in load_blueprint(SMOKE).capabilities]


# -- the setting --------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "on"),
        ("", "on"),
        ("not-a-mode", "on"),
        ("off", "off"),
        (" OFF ", "off"),
        ("shadow", "shadow"),
        ("on", "on"),
    ],
)
def test_the_foreman_is_on_unless_the_setting_says_otherwise(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv(fm.FLAG_ENV, value)
    assert fm.foreman_mode() == expected


@pytest.mark.parametrize("auto_pilot, cycle", [(False, "code"), (True, "pilot")])
def test_the_production_budget_is_three_rounds_per_gate(auto_pilot, cycle):
    assert REWORK_BUDGET == 3 and REWORK_CEILING == 6
    assert _max_rework(auto_pilot=auto_pilot, cycle=cycle) == 3
    assert BuildBudget().max_rework == 3


# -- a recorded gate failure reaches the writer through the foreman ----------


@pytest.mark.parametrize("index, shape", [(0, "missing_field"), (1, "wrong_status")])
def test_a_gate_failure_reaches_the_writer_as_validated_foreman_instructions(
    tmp_path, monkeypatch, index, shape
):
    cap = _caps()[index]
    llm = _Llm(_reply(cap, what=f"accept the {shape} payload the model declares"))
    runner, out, calls = _run(tmp_path, monkeypatch, {T: [_tester_fail(cap, shape)]}, llm=llm)

    assert out.outcome is Outcome.SUCCESS, out.detail
    assert llm.calls == 1
    handed = calls[1]
    foreman_items = [i for i in handed if isinstance(i, Finding) and i.gate == fm.ENTRY]
    assert foreman_items and all(i.capability_id == cap for i in foreman_items)
    assert any(shape in str(i) for i in foreman_items)
    # The raw TESTER finding is replaced, not doubled.
    assert not any(isinstance(i, Finding) and i.gate == "TESTER" for i in handed)
    (rework,) = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert rework.payload["foreman"]["mode"] == "on"
    assert rework.payload["foreman"]["used"] is True


@pytest.mark.parametrize(
    "bad_file", ["tests/test_routes.py", "scripts/acceptance.py"]
)
def test_a_refused_foreman_instruction_never_reaches_the_writer(tmp_path, monkeypatch, bad_file):
    # An instruction aimed at the TESTER's lane or a Factory-owned file is
    # refused twice, so the writer gets the raw typed findings instead.
    cap = _caps()[0]
    llm = _Llm(_reply(cap, file=bad_file))
    runner, out, calls = _run(tmp_path, monkeypatch, {T: [_tester_fail(cap, "refused")]}, llm=llm)

    assert out.outcome is Outcome.SUCCESS, out.detail
    assert llm.calls == 2
    handed = calls[1]
    assert not any(isinstance(i, Finding) and i.gate == fm.ENTRY for i in handed)
    assert any(isinstance(i, Finding) and i.gate == "TESTER" for i in handed)
    (rework,) = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert rework.payload["foreman"]["used"] is False
    assert rework.payload["foreman"]["fallback"] is True
    assert rework.payload["foreman"]["refusals"]


# -- three rounds per gate, not a fourth ------------------------------------------


@pytest.mark.parametrize(
    "role, check",
    [(W, brief_gates.WRITER_BEHAVIOUR_CHECK), (T, brief_gates.SUITE_CHECK)],
)
def test_a_gate_gets_a_second_and_a_third_round(tmp_path, monkeypatch, role, check):
    script = {role: [_fail(role, check, f"shape{i}") for i in range(3)]}
    runner, out, calls = _run(tmp_path, monkeypatch, script)

    assert out.outcome is Outcome.SUCCESS, out.detail
    rounds = [(d["gate"], d["round_gate"]) for d in _decisions(runner, DECISION_REWORK)]
    assert rounds == [(role.value, 1), (role.value, 2), (role.value, 3)]
    assert len(calls) == 4
    assert not _decisions(runner, DECISION_STOP)


@pytest.mark.parametrize(
    "role, check",
    [(W, brief_gates.WRITER_BEHAVIOUR_CHECK), (T, brief_gates.SUITE_CHECK)],
)
def test_a_gate_never_gets_a_fourth_round(tmp_path, monkeypatch, role, check):
    script = {role: [_fail(role, check, f"shape{i}") for i in range(4)]}
    runner, out, calls = _run(tmp_path, monkeypatch, script)

    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail
    assert len(_decisions(runner, DECISION_REWORK)) == 3
    assert len(calls) == 4
    (stop,) = _decisions(runner, DECISION_STOP)
    assert "rework budget of 3 spent" in stop["reason"]
    assert out.detail.startswith(f"FAILED({role.value}, {check}, ")


# -- the ceiling of six still ends the build -----------------------------------


def _n3_failure(runner, check, shape):
    runner.ledger.append(
        EventKind.RUN_FAILED,
        role=S,
        detail="store-gate failed",
        payload={
            "failure_owner": PRODUCT,
            "product_failed": [check],
            "product_failed_detail": {check: f"the gate's evidence for {check} ({shape})"},
        },
    )
    return store_gate_verdict(runner.ledger.terminal_event().payload)


@pytest.mark.parametrize("pick", [0, 1])
def test_the_ceiling_of_six_ends_the_build_though_a_gate_has_rounds_left(tmp_path, monkeypatch, pick):
    # WRITER and TESTER spend three rounds each (six for the build); the
    # Store gate has spent none of its own three, yet its first failure stops
    # the build at the ceiling.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(3)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(3)],
    }
    runner, out, _ = _run(tmp_path, monkeypatch, script)
    assert out.outcome is Outcome.SUCCESS, out.detail
    assert len(_decisions(runner, DECISION_REWORK)) == REWORK_CEILING
    assert runner._rework_rounds_at(S) == 0

    check = [c for c in enforced_ids(load_blueprint(SMOKE)) if owner_of(c) == PRODUCT][pick]
    decision = runner.reopen_after_store_gate(_n3_failure(runner, check, f"s{pick}"))

    assert decision.kind == DECISION_STOP
    (stop,) = _decisions(runner, DECISION_STOP)
    assert stop["round_build"] == REWORK_CEILING and "ceiling" in stop["reason"]
    assert len(_decisions(runner, DECISION_REWORK)) == REWORK_CEILING


@pytest.mark.parametrize("pick", [0, 1])
def test_below_the_ceiling_the_store_gate_still_gets_its_round(tmp_path, monkeypatch, pick):
    # The negative case: five rounds spent, the Store gate's failure is a REWORK.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(3)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(2)],
    }
    runner, out, _ = _run(tmp_path, monkeypatch, script)
    assert out.outcome is Outcome.SUCCESS, out.detail
    check = [c for c in enforced_ids(load_blueprint(SMOKE)) if owner_of(c) == PRODUCT][pick]

    decision = runner.reopen_after_store_gate(_n3_failure(runner, check, f"s{pick}"))

    assert decision.kind == DECISION_REWORK
    assert decision.record["round_build"] == REWORK_CEILING


# -- the grader's rounds count toward the ceiling of six ------------------------


def _no_model():
    from app.core.model_ladder import NOT_CONFIGURED, ModelUnavailable

    def call(messages):
        raise ModelUnavailable(NOT_CONFIGURED, "no model in this test")

    return call


def _grade_script(monkeypatch, certified_first):
    """The in-build grader: CERTIFIED (None) for its first ``certified_first``
    calls, NOT_CERTIFIED on every call after."""
    from app.factory.build import grader as grader_mod

    calls = []

    def writer_grader(self):
        calls.append(1)
        if len(calls) <= certified_first:
            return None
        item = f"function grade FAIL: {_caps()[0]} (call {len(calls)})"
        return grader_mod.GraderVerdict(
            tier=grader_mod.NOT_CERTIFIED, items=(item,), reasons=(), writer_items=(item,)
        )

    monkeypatch.setattr(RoleRunner, "_writer_grader", writer_grader)
    monkeypatch.setattr(RoleRunner, "_grader_factory_fault", lambda self, v, used: None)
    return calls


@pytest.mark.parametrize("writer_fails, tester_fails", [(3, 1), (2, 2)])
def test_the_ceiling_of_six_ends_the_build_at_a_grader_round(
    tmp_path, monkeypatch, writer_fails, tester_fails
):
    # Four gate rounds, then the grader refuses every tree: it gets build
    # rounds five and six, and its third refusal meets the ceiling, though
    # its own budget of three is not spent.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    grades = _grade_script(monkeypatch, certified_first=tester_fails)
    runner, out, _ = _run(tmp_path, monkeypatch, script, llm=_no_model())

    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail
    assert out.detail.startswith(
        f"GRADER: NOT_CERTIFIED -- build rework ceiling of {REWORK_CEILING} reached after 2 grader round(s)"
    )
    reworks = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert len(reworks) == REWORK_CEILING
    graded = [e.payload for e in reworks if e.payload.get("source") == "GRADER"]
    assert [(p["round"], p["round_build"]) for p in graded] == [(1, 5), (2, 6)]
    assert "round 2/3 (build 6/6)" in reworks[-1].detail
    assert len(grades) == tester_fails + 3
    (stop,) = [
        e for e in runner.ledger.events()
        if e.kind is EventKind.GATE_FAILED and (e.payload or {}).get("reason") == "grader_build_ceiling"
    ]
    assert stop.role is W


@pytest.mark.parametrize("writer_fails, tester_fails", [(1, 1), (0, 2)])
def test_below_the_ceiling_the_grader_spends_its_own_three_rounds(
    tmp_path, monkeypatch, writer_fails, tester_fails
):
    # The negative case: two gate rounds, so the grader's three rounds end at
    # build round five and its own budget stops the run, not the ceiling.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    _grade_script(monkeypatch, certified_first=tester_fails)
    runner, out, _ = _run(tmp_path, monkeypatch, script, llm=_no_model())

    assert out.outcome is Outcome.FAILED_GATE, out.detail
    assert out.detail.startswith(f"GRADER: NOT_CERTIFIED after {REWORK_BUDGET} round(s)")
    graded = [
        e.payload for e in runner.ledger.events()
        if e.kind is EventKind.REWORK and e.payload.get("source") == "GRADER"
    ]
    assert [(p["round"], p["round_build"]) for p in graded] == [(1, 3), (2, 4), (3, 5)]


# -- what the owner is told about a grader round and a grader ceiling stop ------


def _fix_rounds_numbers(decisions):
    """The numbers the Floor's "Fix rounds used: b of c (step: g of n)" line is
    written from (frontend buildProgress.fixRoundsLine): the LAST decision of
    class REWORK -- its build round, the ceiling, its step, its gate round and
    that gate's budget."""
    last = [d for d in decisions if d["class"] == DECISION_REWORK][-1]
    return last["round_build"], last["build_ceiling"], last["gate"], last["round_gate"], last["gate_budget"]


def _grade_item(call):
    return f"function grade FAIL: {_caps()[0]} (call {call})"


@pytest.mark.parametrize(
    "writer_fails, tester_fails, max_rework",
    [(3, 1, REWORK_BUDGET), (2, 2, REWORK_BUDGET), (2, 2, 2)],
)
def test_a_grader_ceiling_stop_tells_the_owner_the_fix_rounds_are_spent(
    tmp_path, monkeypatch, writer_fails, tester_fails, max_rework
):
    # Four gate rounds, then the grader refuses every tree. The build status
    # the Floor reads must say what happened: a check refused the work and the
    # six fix rounds are spent -- never "ran out of time" -- and must hold a
    # decision for every round, the grader's two included, and the STOP.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    _grade_script(monkeypatch, certified_first=tester_fails)
    runner, out, _ = _run(
        tmp_path, monkeypatch, script, llm=_no_model(), budget=BuildBudget(max_rework=max_rework)
    )
    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail

    status = build_status(tmp_path / "build")
    failure = status["failure"]
    assert status["state"] == "failed"
    assert failure["kind"] == fr.ROUNDS_SPENT == "rounds_spent"
    assert failure["title"] == "used up its fix rounds"
    assert failure["summary"] == (
        "A check still did not pass the work (1 finding), and this build has no fix rounds left."
    )
    assert failure["next"] == (
        "Continuing does not grant new fix rounds: the step runs again and stops again if "
        "the same check fails."
    )
    for said in (failure["title"], failure["summary"], failure["next"]):
        assert "time" not in said and "No check refused" not in said

    decisions = status["decisions"]
    assert [d["class"] for d in decisions] == [DECISION_REWORK] * REWORK_CEILING + [DECISION_STOP]
    assert [d["round_build"] for d in decisions] == [1, 2, 3, 4, 5, 6, REWORK_CEILING]
    # The four gate rounds are counted against the build's own budget ...
    assert {(d["gate_name"], d["gate_budget"]) for d in decisions[:4]} == {
        ("writer_gate", max_rework),
        ("tester_gate", max_rework),
    }
    # ... the grader's two against the grader's, as rounds five and six.
    graded = [d for d in decisions if d["class"] == DECISION_REWORK and d["gate_name"] == GRADER_GATE]
    assert [
        (d["gate"], d["check"], d["round_gate"], d["gate_budget"], d["round_build"], d["build_ceiling"])
        for d in graded
    ] == [
        ("WRITER", GRADER_GATE, 1, REWORK_BUDGET, 5, REWORK_CEILING),
        ("WRITER", GRADER_GATE, 2, REWORK_BUDGET, 6, REWORK_CEILING),
    ]
    assert [d["finding"] for d in graded] == [_grade_item(tester_fails + 1), _grade_item(tester_fails + 2)]

    stop = decisions[-1]
    assert (stop["gate"], stop["gate_name"], stop["check"]) == ("WRITER", GRADER_GATE, GRADER_GATE)
    assert (stop["round_gate"], stop["gate_budget"]) == (2, REWORK_BUDGET)
    assert stop["round_build"] == REWORK_CEILING == stop["build_ceiling"]
    assert stop["finding"] == _grade_item(tester_fails + 3)
    assert stop["reason"].startswith(
        f"build rework ceiling of {REWORK_CEILING} reached; GRADER gate still failing: "
    )
    # The STOP is the run's ONE terminal event, as decide()'s own stop is.
    terminal = runner.ledger.terminal_event()
    assert terminal.kind is EventKind.RUN_FAILED
    assert terminal.payload["decision"]["class"] == DECISION_STOP
    assert status["stopped"] == stop
    assert status["failed"] == {"gate": "WRITER", "check": GRADER_GATE, "finding": stop["finding"]}

    # "Fix rounds used: 6 of 6", and the stop record counts six, not eight.
    assert _fix_rounds_numbers(decisions) == (REWORK_CEILING, REWORK_CEILING, "WRITER", 2, REWORK_BUDGET)
    assert status["stop"]["fix_rounds"] == REWORK_CEILING


@pytest.mark.parametrize("writer_fails, tester_fails", [(1, 1), (0, 2)])
def test_every_grader_round_is_a_decision_the_floor_counts(
    tmp_path, monkeypatch, writer_fails, tester_fails
):
    # Below the ceiling: the grader's own three rounds each write a REWORK
    # decision, so the Floor counts five fix rounds, not two. Its own-budget
    # stop is not the ceiling's: no STOP record, the outcome stays FAILED_GATE.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    _grade_script(monkeypatch, certified_first=tester_fails)
    runner, out, _ = _run(tmp_path, monkeypatch, script, llm=_no_model())
    assert out.outcome is Outcome.FAILED_GATE, out.detail

    status = build_status(tmp_path / "build")
    decisions = status["decisions"]
    assert [d["class"] for d in decisions] == [DECISION_REWORK] * 5
    assert [d["round_build"] for d in decisions] == [1, 2, 3, 4, 5]
    graded = [d for d in decisions if d["gate_name"] == GRADER_GATE]
    assert [(d["gate"], d["round_gate"], d["gate_budget"], d["round_build"]) for d in graded] == [
        ("WRITER", 1, REWORK_BUDGET, 3),
        ("WRITER", 2, REWORK_BUDGET, 4),
        ("WRITER", 3, REWORK_BUDGET, 5),
    ]
    assert [d["finding"] for d in graded] == [_grade_item(tester_fails + n) for n in (1, 2, 3)]
    # The record on the status is the one the REWORK event itself carries.
    events = [
        e.payload["decision"] for e in runner.ledger.events()
        if e.kind is EventKind.REWORK and e.payload.get("source") == "GRADER"
    ]
    assert events == graded
    assert status["stopped"] is None
    assert _fix_rounds_numbers(decisions) == (5, REWORK_CEILING, "WRITER", REWORK_BUDGET, REWORK_BUDGET)
    assert status["stop"]["fix_rounds"] == 5


@pytest.mark.parametrize("writer_fails, max_rework", [(0, REWORK_BUDGET), (2, 2)])
def test_a_gate_stop_still_records_that_gates_own_rounds_and_budget(
    tmp_path, monkeypatch, writer_fails, max_rework
):
    # The control: a gate decide() judges. Its STOP record counts the gate's
    # own rounds against the build's budget, and reads as fix rounds spent.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(max_rework + 1)],
    }
    runner, out, _ = _run(
        tmp_path, monkeypatch, script, llm=_no_model(), budget=BuildBudget(max_rework=max_rework)
    )
    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail

    status = build_status(tmp_path / "build")
    stop = status["decisions"][-1]
    assert (stop["class"], stop["gate"], stop["gate_name"]) == (DECISION_STOP, "TESTER", "tester_gate")
    assert (stop["round_gate"], stop["gate_budget"]) == (max_rework, max_rework)
    assert stop["round_build"] == writer_fails + max_rework
    assert status["failure"]["kind"] == fr.ROUNDS_SPENT
    assert status["failure"]["title"] == "used up its fix rounds"


@pytest.mark.parametrize("writer_fails, tester_fails, max_rework", [(3, 3, REWORK_BUDGET), (2, 4, 4)])
def test_the_ceiling_met_before_any_grader_round_stops_with_no_grader_round(
    tmp_path, monkeypatch, writer_fails, tester_fails, max_rework
):
    # Six gate rounds, then the grader's FIRST refusal: the build has no round
    # left, so the grader sends nothing back. The STOP counts zero grader
    # rounds of its three -- a zero the record keeps, never the writer gate's
    # own count -- and the last fix round the Floor reads is the tester's.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    grades = _grade_script(monkeypatch, certified_first=tester_fails)
    runner, out, _ = _run(
        tmp_path, monkeypatch, script, llm=_no_model(), budget=BuildBudget(max_rework=max_rework)
    )
    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail
    assert "reached after 0 grader round(s)" in out.detail
    assert len(grades) == tester_fails + 1

    status = build_status(tmp_path / "build")
    decisions = status["decisions"]
    assert [d["class"] for d in decisions] == [DECISION_REWORK] * REWORK_CEILING + [DECISION_STOP]
    assert not [d for d in decisions[:-1] if d["gate_name"] == GRADER_GATE]
    stop = decisions[-1]
    assert (stop["gate"], stop["gate_name"]) == ("WRITER", GRADER_GATE)
    assert (stop["round_gate"], stop["gate_budget"]) == (0, REWORK_BUDGET)
    assert stop["round_build"] == REWORK_CEILING
    assert status["failure"]["kind"] == fr.ROUNDS_SPENT
    assert status["stop"]["kind"] == fr.STOP_CHECK_FAILED
    assert _fix_rounds_numbers(decisions) == (
        REWORK_CEILING, REWORK_CEILING, "TESTER", tester_fails, max_rework
    )
    assert status["stop"]["fix_rounds"] == REWORK_CEILING


# -- the rule's own functions, each called directly -----------------------------


def _seeded(tmp_path, sources, *, max_rework=REWORK_BUDGET):
    """A runner that has not run, its ledger holding one REWORK round per
    entry of ``sources`` (the gate that sent the round back)."""
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        roles={role: (lambda ctx: RoleResult(ok=True, detail="stub")) for role in BUILD_PHASES},
        budget=BuildBudget(max_rework=max_rework),
        brief=BRIEF,
    )
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(product_id="seeded", inputs_hash=runner.inputs_hash)
    for n, source in enumerate(sources):
        runner.ledger.append(
            EventKind.REWORK,
            role=W,
            detail=f"seeded round {n + 1}",
            payload={"source": source, "failure_names": [f"seeded-{n}"]},
        )
    return runner


@pytest.mark.parametrize(
    "sources, source",
    [
        ((), ""),
        (("TESTER",) * (REWORK_CEILING - 1), ""),
        (("WRITER", "TESTER", "GRADER", "GRADER", "GRADER"), "GRADER"),
    ],
)
def test_ceiling_stop_is_no_stop_while_the_build_has_a_round_left(tmp_path, sources, source):
    runner = _seeded(tmp_path, sources)
    before = len(runner.ledger.events())
    verdict = _fail(T, brief_gates.SUITE_CHECK, "x")

    assert runner._ceiling_stop(T, verdict, source=source, gate_round=1, gate_budget=3) is None
    assert len(runner.ledger.events()) == before


@pytest.mark.parametrize(
    "sources, source, gate_round, gate_budget, want_round, want_budget, named",
    [
        # A gate decide() judges: its own rounds, the build's budget.
        (("WRITER",) * 3 + ("TESTER",) * 3, "", None, None, 3, 2, "TESTER"),
        (("WRITER",) * 5 + ("TESTER",) * 2, "", None, None, 2, 2, "TESTER"),
        # The grader's handback: the rounds and the budget it counts itself.
        (("WRITER",) * 3 + ("TESTER", "GRADER", "GRADER"), "GRADER", 2, REWORK_BUDGET, 2, REWORK_BUDGET, "GRADER"),
        # Zero grader rounds is a count, not "not given".
        (("WRITER",) * 3 + ("TESTER",) * 3, "GRADER", 0, REWORK_BUDGET, 0, REWORK_BUDGET, "GRADER"),
    ],
)
def test_ceiling_stop_is_the_rules_stop_once_six_rounds_are_spent(
    tmp_path, sources, source, gate_round, gate_budget, want_round, want_budget, named
):
    runner = _seeded(tmp_path, sources, max_rework=2)
    before = len(runner.ledger.events())
    verdict = _fail(T, brief_gates.SUITE_CHECK, "x")

    got = runner._ceiling_stop(T, verdict, source=source, gate_round=gate_round, gate_budget=gate_budget)

    reason = (
        f"build rework ceiling of {REWORK_CEILING} reached; {named} gate still failing: {verdict.detail}"
    )
    assert got is not None
    assert (got.kind, got.outcome) == (DECISION_STOP, Outcome.FAILED_BUDGET_SPENT)
    assert got.record == {
        "gate": "TESTER",
        "check": brief_gates.SUITE_CHECK,
        "finding": verdict.findings[0],
        "class": DECISION_STOP,
        "round_gate": want_round,
        "round_build": len(sources),
        "gate_budget": want_budget,
        "build_ceiling": REWORK_CEILING,
        "gate_name": "tester_gate",
        "reason": reason,
    }
    assert got.detail == f"FAILED(TESTER, {brief_gates.SUITE_CHECK}, {verdict.findings[0]}): {reason}"
    assert got.findings == tuple(verdict.findings)
    # The stop is what the caller ends the run with; asking writes nothing.
    assert len(runner.ledger.events()) == before
    # It is the record the Floor reads as fix rounds spent, not as time.
    assert fr.stop_class({"outcome": got.outcome.value, "decision": got.record}) == fr.ROUNDS_SPENT


@pytest.mark.parametrize(
    "gate_round, gate_budget, max_rework, want_round, want_budget",
    [
        (None, None, 3, 2, 3),
        (None, None, 5, 2, 5),
        (1, REWORK_BUDGET, 5, 1, REWORK_BUDGET),
        (0, 0, 5, 0, 0),
    ],
)
def test_stop_counts_the_round_and_budget_it_is_given_else_the_roles_own(
    tmp_path, gate_round, gate_budget, max_rework, want_round, want_budget
):
    runner = _seeded(tmp_path, ("WRITER", "TESTER", "TESTER", "GRADER"), max_rework=max_rework)
    verdict = _fail(T, brief_gates.SUITE_CHECK, "y")

    got = runner._stop(
        Outcome.FAILED_GATE, "why it stopped", verdict, T, gate_round=gate_round, gate_budget=gate_budget
    )

    assert (got.kind, got.outcome) == (DECISION_STOP, Outcome.FAILED_GATE)
    assert got.record == {
        "gate": "TESTER",
        "check": brief_gates.SUITE_CHECK,
        "finding": verdict.findings[0],
        "class": DECISION_STOP,
        "round_gate": want_round,
        "round_build": 4,
        "gate_budget": want_budget,
        "build_ceiling": REWORK_CEILING,
        "gate_name": "tester_gate",
        "reason": "why it stopped",
    }
    assert got.detail == f"FAILED(TESTER, {brief_gates.SUITE_CHECK}, {verdict.findings[0]}): why it stopped"


@pytest.mark.parametrize(
    "kind, role, gate_budget, max_rework, want_budget",
    [
        (DECISION_REWORK, W, None, 3, 3),
        (DECISION_REWORK, T, None, 2, 2),
        (DECISION_REWORK, W, REWORK_BUDGET, 2, REWORK_BUDGET),
        (DECISION_STOP, T, 0, 2, 0),
    ],
)
def test_decision_record_counts_the_round_against_the_budget_it_is_given(
    tmp_path, kind, role, gate_budget, max_rework, want_budget
):
    runner = _seeded(tmp_path, (), max_rework=max_rework)
    verdict = _fail(role, brief_gates.SUITE_CHECK, "z")

    rec = runner._decision_record(
        kind, role=role, verdict=verdict, gate_round=1, build_round=5, reason="r", gate_budget=gate_budget
    )

    assert rec == {
        "gate": role.value,
        "check": brief_gates.SUITE_CHECK,
        "finding": verdict.findings[0],
        "class": kind,
        "round_gate": 1,
        "round_build": 5,
        "gate_budget": want_budget,
        "build_ceiling": REWORK_CEILING,
        "gate_name": verdict.gate,
        "reason": "r",
    }


@pytest.mark.parametrize(
    "items, writer_items, detail",
    [
        (("one failing item",), ("one failing item",), "one failing item"),
        # Only what the writer can repair is in the round's record.
        (("a: first", "b: the Factory's", "c: third"), ("a: first", "c: third"), "a: first; c: third"),
        (("x" * 500,), ("x" * 500,), "x" * 400),
        (("nothing for the writer",), (), "-"),
    ],
)
def test_grader_gate_verdict_is_the_graders_refusal_in_the_rules_shape(
    tmp_path, items, writer_items, detail
):
    from app.factory.build import grader as grader_mod

    runner = _seeded(tmp_path, ())
    graded = grader_mod.GraderVerdict(
        tier=grader_mod.NOT_CERTIFIED, items=items, reasons=(), writer_items=writer_items
    )

    got = runner._grader_gate_verdict(graded)

    assert isinstance(got, GateResult)
    assert (got.ok, got.gate, got.reason) == (False, GRADER_GATE, grader_mod.NOT_CERTIFIED)
    assert got.findings == list(writer_items)
    assert got.detail == detail
    # A view only: the grader's own verdict is as it was.
    assert (graded.items, graded.writer_items) == (items, writer_items)
    # The round's record names the grader's gate and the first item handed over.
    rec = runner._decision_record(DECISION_REWORK, role=W, verdict=got, gate_round=1, build_round=1)
    assert (rec["gate_name"], rec["check"]) == (GRADER_GATE, GRADER_GATE)
    assert rec["finding"] == (writer_items[0][:300] if writer_items else "-")


@pytest.mark.parametrize("spent", [0, REWORK_CEILING - 2, REWORK_CEILING - 1])
def test_decide_grants_a_round_while_the_build_has_one_left(tmp_path, monkeypatch, spent):
    # decide(), called directly on a gate with rounds of its own left: up to
    # the sixth round of the build it sends the work back.
    monkeypatch.setattr(architect, "_default_llm", lambda: _no_model())
    runner = _seeded(tmp_path, ("TESTER",) * spent, max_rework=REWORK_CEILING + 5)
    verdict = _fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, "direct")

    decision = runner.decide(W, verdict, rework_used=spent)

    assert decision.kind == DECISION_REWORK
    assert (decision.record["round_gate"], decision.record["round_build"]) == (1, spent + 1)
    reworks = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert len(reworks) == spent + 1
    assert reworks[-1].payload["decision"] == decision.record


@pytest.mark.parametrize("spent", [REWORK_CEILING, REWORK_CEILING + 1])
def test_decide_refuses_a_seventh_round_through_the_one_ceiling_check(tmp_path, monkeypatch, spent):
    # The same gate with the build's six rounds spent: only the ceiling can
    # stop it (its own budget is far off), and the stop is _ceiling_stop's.
    monkeypatch.setattr(architect, "_default_llm", lambda: _no_model())
    runner = _seeded(tmp_path, ("TESTER",) * spent, max_rework=REWORK_CEILING + 5)
    verdict = _fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, "direct")

    decision = runner.decide(W, verdict, rework_used=spent)

    assert (decision.kind, decision.outcome) == (DECISION_STOP, Outcome.FAILED_BUDGET_SPENT)
    assert decision.record == runner._ceiling_stop(W, verdict).record
    assert decision.record["round_build"] == spent
    assert decision.record["reason"].startswith(f"build rework ceiling of {REWORK_CEILING} reached; WRITER gate")
    assert len([e for e in runner.ledger.events() if e.kind is EventKind.REWORK]) == spent


@pytest.mark.parametrize(
    "check, kind, rounds",
    [
        ("zorblat_check", "ADVISORY", 0),
        ("plinket_check", "ADVISORY", 0),
        (brief_gates.WRITER_BEHAVIOUR_CHECK, DECISION_REWORK, 1),
    ],
)
def test_decide_spends_no_round_on_a_check_the_brief_never_defined(
    tmp_path, monkeypatch, check, kind, rounds
):
    monkeypatch.setattr(architect, "_default_llm", lambda: _no_model())
    runner = _seeded(tmp_path, ())

    decision = runner.decide(W, _fail(W, check, "direct"), rework_used=0)

    assert decision.kind == kind
    assert decision.record["check"] == check and decision.record["class"] == kind
    assert len([e for e in runner.ledger.events() if e.kind is EventKind.REWORK]) == rounds


def _send_back(gate, decision):
    payload = {"gate": gate, "source": "GRADER" if gate == GRADER_GATE else "TESTER"}
    if decision is not None:
        payload["decision"] = decision
    return payload


_REC = {"class": DECISION_REWORK, "gate": "WRITER", "round_build": 1}


@pytest.mark.parametrize(
    "payloads, want, once",
    [
        ((), 0, 0),
        # A ledger written before the grader's handback carried a record.
        ((_send_back(GRADER_GATE, None),), 1, 1),
        ((_send_back(GRADER_GATE, None), _send_back(GRADER_GATE, None)), 2, 2),
        # Today's ledger: the record counts the round, so it is not counted here.
        ((_send_back(GRADER_GATE, _REC),), 0, 1),
        # A build resumed across the change: one round of each kind.
        ((_send_back(GRADER_GATE, None), _send_back(GRADER_GATE, _REC)), 1, 2),
        # Another gate's round is never the grader's, with or without a record.
        ((_send_back("tester_gate", None), _send_back("tester_gate", _REC)), 0, 1),
        # A value under the key that is no record counts no round for it.
        ((_send_back(GRADER_GATE, "hard_stop"),), 1, 1),
    ],
)
def test_grader_send_backs_counts_only_the_rounds_no_decision_record_counts(
    tmp_path, payloads, want, once
):
    from app.factory.build_jobs import _grader_send_backs

    runner = _seeded(tmp_path, ())
    for payload in payloads:
        runner.ledger.append(EventKind.REWORK, role=W, detail="send-back", payload=payload)
        # A grader event that is not a REWORK is never a round.
        runner.ledger.append(EventKind.NOTE, role=W, detail="grader note", payload={"gate": GRADER_GATE})
    events = runner.ledger.events()

    assert _grader_send_backs(events) == want
    # ``once``: with the records the status lists, the stop record's total
    # (build_jobs.stop_record) counts every round one time, none twice.
    recorded = [e.payload["decision"] for e in events if isinstance(e.payload.get("decision"), dict)]
    assert fr.fix_rounds_used(recorded) + _grader_send_backs(events) == once


# -- the foreman's STOP under the default --------------------------------------


def _stop_reply(cap, confidence, reason):
    body = _reply(cap)
    body.update(recommend="stop", confidence=confidence, stop_reason=reason)
    return body


@pytest.mark.parametrize(
    "index, reason",
    [(0, "the handler and its model disagree by design"), (1, "the brief asks for two answers")],
)
def test_the_foreman_stop_at_its_confidence_ends_the_build(tmp_path, monkeypatch, index, reason):
    cap = _caps()[index]
    llm = _Llm(_stop_reply(cap, fm.STOP_CONFIDENCE, reason))
    runner, out, calls = _run(tmp_path, monkeypatch, {T: [_tester_fail(cap, "stopped")]}, llm=llm)

    assert out.outcome is Outcome.FAILED_GATE, out.detail
    assert f"foreman:{reason}" in out.detail
    assert len(calls) == 1
    assert not _decisions(runner, DECISION_REWORK)


@pytest.mark.parametrize("index, confidence", [(0, 0.79), (1, 0.5)])
def test_a_foreman_stop_below_its_confidence_is_not_honoured(tmp_path, monkeypatch, index, confidence):
    cap = _caps()[index]
    llm = _Llm(_stop_reply(cap, confidence, "not sure enough"))
    runner, out, calls = _run(tmp_path, monkeypatch, {T: [_tester_fail(cap, "kept")]}, llm=llm)

    assert out.outcome is Outcome.SUCCESS, out.detail
    assert [(d["gate"], d["round_gate"]) for d in _decisions(runner, DECISION_REWORK)] == [(T.value, 1)]
    assert len(calls) == 2


# -- with no model reachable the default still builds --------------------------


@pytest.mark.parametrize("index, shape", [(0, "missing_field"), (1, "wrong_status")])
def test_with_no_model_the_writer_gets_the_raw_findings(tmp_path, monkeypatch, index, shape):
    cap = _caps()[index]
    runner, out, calls = _run(tmp_path, monkeypatch, {T: [_tester_fail(cap, shape)]}, llm=_no_model())

    assert out.outcome is Outcome.SUCCESS, out.detail
    (rework,) = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    foreman = rework.payload["foreman"]
    assert foreman["mode"] == "on" and foreman["used"] is False and foreman["fallback"] is True
    assert foreman["refusals"] == ["foreman call failed: ModelUnavailable"]
    handed = calls[1]
    assert any(isinstance(i, Finding) and i.gate == "TESTER" and shape in str(i) for i in handed)


# -- the failure narrative follows the same setting -----------------------------


def _narrative_calls(runner):
    from app.factory.build import failure_narrative as fn

    return [
        e.payload["architect"] for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and ((e.payload or {}).get("architect") or {}).get("entry") == fn.ENTRY
    ]


def _narrative(runner):
    from app.factory.build import failure_narrative as fn

    (note,) = [
        e.payload[fn.NARRATIVE_KEY] for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and fn.NARRATIVE_KEY in (e.payload or {})
    ]
    return note


@pytest.mark.parametrize(
    "role, check", [(W, brief_gates.WRITER_BEHAVIOUR_CHECK), (T, brief_gates.SUITE_CHECK)]
)
def test_a_failed_run_asks_the_model_for_its_narrative_by_default(tmp_path, monkeypatch, role, check):
    script = {role: [_fail(role, check, f"shape{i}") for i in range(4)]}
    runner, out, _ = _run(tmp_path, monkeypatch, script, llm=_no_model())

    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail
    calls = _narrative_calls(runner)
    assert [c["mode"] for c in calls] == ["on", "on"]
    assert calls[-1]["fallback"] is True
    # no model answered, so the ledger-rendered narrative stands
    assert _narrative(runner)["source"] == "ledger"


@pytest.mark.parametrize(
    "role, check", [(W, brief_gates.WRITER_BEHAVIOUR_CHECK), (T, brief_gates.SUITE_CHECK)]
)
def test_with_the_foreman_off_a_failed_run_makes_no_narrative_call(tmp_path, monkeypatch, role, check):
    monkeypatch.setenv(fm.FLAG_ENV, "off")
    llm = _Llm(_reply(_caps()[0]))
    script = {role: [_fail(role, check, f"shape{i}") for i in range(4)]}
    runner, out, _ = _run(tmp_path, monkeypatch, script, llm=llm)

    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail
    assert llm.calls == 0
    assert _narrative_calls(runner) == []
    assert _narrative(runner)["source"] == "ledger"


@pytest.mark.parametrize("writer_fails, tester_fails", [(2, 1), (1, 2), (3, 0)])
def test_the_graders_last_round_being_the_builds_last_stops_as_rounds_spent(
    tmp_path, monkeypatch, writer_fails, tester_fails
):
    # Verdict a7946d0 fault 1: three gate rounds, then the grader refuses every
    # tree. Its third round is the build's sixth, so its next refusal meets the
    # grader's own budget AND the build's ceiling at once. The build has no fix
    # round left: the stop is the rule's STOP (rounds_spent), never "a check
    # refused, using the fix rounds this build has left".
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    _grade_script(monkeypatch, certified_first=tester_fails)
    runner, out, _ = _run(tmp_path, monkeypatch, script, llm=_no_model(), budget=BuildBudget(max_rework=REWORK_BUDGET))
    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail

    status = build_status(tmp_path / "build")
    failure = status["failure"]
    assert (failure["kind"], failure["title"]) == (fr.ROUNDS_SPENT, "used up its fix rounds")
    assert failure["next"] == (
        "Continuing does not grant new fix rounds: the step runs again and stops again if "
        "the same check fails."
    )
    assert "has left" not in failure["next"] and "has left" not in failure["summary"]

    decisions = status["decisions"]
    assert [d["class"] for d in decisions] == [DECISION_REWORK] * REWORK_CEILING + [DECISION_STOP]
    assert [d["round_build"] for d in decisions] == [1, 2, 3, 4, 5, 6, REWORK_CEILING]
    graded = [d for d in decisions if d["class"] == DECISION_REWORK and d["gate_name"] == GRADER_GATE]
    assert [(d["round_gate"], d["gate_budget"], d["round_build"]) for d in graded] == [
        (1, REWORK_BUDGET, 4),
        (2, REWORK_BUDGET, 5),
        (3, REWORK_BUDGET, 6),
    ]
    stop = decisions[-1]
    assert (stop["gate_name"], stop["round_gate"], stop["gate_budget"]) == (GRADER_GATE, REWORK_BUDGET, REWORK_BUDGET)
    assert stop["round_build"] == REWORK_CEILING == stop["build_ceiling"]
    assert runner.ledger.terminal_event().payload["decision"]["class"] == DECISION_STOP
    assert status["stop"]["fix_rounds"] == REWORK_CEILING


@pytest.mark.parametrize("writer_fails, tester_fails", [(1, 1), (0, 2), (0, 0)])
def test_the_graders_budget_spent_below_the_ceiling_still_reads_as_a_refused_check(
    tmp_path, monkeypatch, writer_fails, tester_fails
):
    # The negative: the grader's three rounds spent with build rounds left is
    # NOT the ceiling. It stays a refused check, and "the fix rounds this build
    # has left" is then true.
    script = {
        W: [_fail(W, brief_gates.WRITER_BEHAVIOUR_CHECK, f"w{i}") for i in range(writer_fails)],
        T: [_fail(T, brief_gates.SUITE_CHECK, f"t{i}") for i in range(tester_fails)],
    }
    _grade_script(monkeypatch, certified_first=tester_fails)
    _runner, out, _ = _run(tmp_path, monkeypatch, script, llm=_no_model(), budget=BuildBudget(max_rework=REWORK_BUDGET))
    assert out.outcome is Outcome.FAILED_GATE, out.detail

    status = build_status(tmp_path / "build")
    assert status["failure"]["kind"] == "check_refused"
    rounds = [d for d in status["decisions"] if d["class"] == DECISION_REWORK]
    assert len(rounds) == writer_fails + tester_fails + REWORK_BUDGET < REWORK_CEILING
    assert [d["class"] for d in status["decisions"]].count(DECISION_STOP) == 0
