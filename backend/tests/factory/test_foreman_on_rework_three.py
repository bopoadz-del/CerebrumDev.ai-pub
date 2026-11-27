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
from app.factory.build import foreman as fm
from app.factory.build import runner as runner_mod
from app.factory.build.acceptance_floor import PRODUCT, enforced_ids, owner_of
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.findings import Finding
from app.factory.build.gates import GateResult
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
from app.factory.build_jobs import _max_rework

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
