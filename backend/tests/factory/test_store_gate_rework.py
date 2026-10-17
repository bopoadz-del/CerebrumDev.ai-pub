"""The runner's ONE rule for a failed phase verdict, at every gate; and one
acceptance source for the writer and the Store gate.

Live 2026-10-05 (665da6da): the CodeWhale writer started with no harness,
wrote its own ("scripts/acceptance.py: 15 measured checks, k/k") and
self-verified against it; the Factory stamped the 22-check harness only after
the writer; the Store gate then failed three product checks the writer never
measured and ended the run with no writer round. Owner rule: every gate's
failure is classified from data (advisory / regenerate test / rework / stop);
2 rework rounds PER GATE, a 6-round ceiling per build; the same check failing
the same way twice stops at once, at any gate; a stop reads
FAILED(<gate>, <check>, <finding>) and is never resumed into a fresh workspace.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import brief_gates
from app.factory.build import roles_handlers
from app.factory.build import runner as runner_mod
from app.factory.build.acceptance_floor import PRODUCT, enforced_ids, owner_of
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.brief_compiler import store_gate_acceptance_line
from app.factory.build.factory_refresh import refresh_factory_files
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.n3_store_gate import store_gate_verdict
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import (
    DECISION_REWORK,
    DECISION_STOP,
    REWORK_CEILING,
    BuildBudget,
    Outcome,
    RoleRunner,
)
from app.factory.build.store_acceptance import (
    ACCEPTANCE_SCRIPT_REL,
    ACCEPTANCE_SELF_CHECK_COMMAND,
    GATE_NAME,
    factory_renders,
)
from app.factory.build_jobs import stopped_by_rule

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
INVENTED = "zorblat_check"
GATE_ONLY = ("STORE_POSTGRES_BOOT", "STORE_AUDIT_CLEAN", "STORE_DOCKER_HEALTH")


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)


@pytest.fixture(scope="module")
def blueprint():
    return load_blueprint(SMOKE)


def _product_checks(blueprint, n=2):
    ids = [c for c in enforced_ids(blueprint) if owner_of(c) == PRODUCT]
    assert len(ids) >= n, ids
    return ids[:n]


# -- scripted runs: stub roles, scripted gate verdicts per phase -------------


def _fail(role, check, shape="failed", finding=None):
    """A failed verdict on one brief check, with a typed finding shape."""
    return GateResult(
        ok=False,
        gate=f"{role.value.lower()}_gate",
        reason="scripted",
        detail=f"{check} failed",
        findings=[finding or f"{check}: scripted finding ({shape})"],
        payload={"check": check, "finding_checks": [check], "finding_shape": shape},
    )


def _run(blueprint, tmp_path, monkeypatch, script, *, max_rework=2):
    """Run every phase as a stub; the gate of role R answers ``script[R]``'s
    verdicts in order, then passes. Returns (runner, outcome, writer calls)."""
    queues = {role: list(script.get(role, ())) for role in BUILD_PHASES}
    writer_calls = []

    def gate_for(role):
        role = BuildRole(role)

        def gate(ctx):
            if queues[role]:
                return queues[role].pop(0)
            return GateResult(ok=True, gate=f"{role.value.lower()}_gate", detail="pass")

        return gate

    def stub(role):
        def run(ctx):
            if role is BuildRole.WRITER:
                writer_calls.append(tuple(ctx.work_list))
            return RoleResult(ok=True, detail="stub")

        return run

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    roles = {role: stub(role) for role in BUILD_PHASES}
    runner = RoleRunner(
        blueprint,
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(max_rework=max_rework),
    )
    return runner, runner.run(), writer_calls


def _decisions(runner, kind=None):
    out = []
    for event in runner.ledger.events():
        rec = (event.payload or {}).get("decision")
        if isinstance(rec, dict) and (kind is None or rec["class"] == kind):
            out.append(rec)
    return out


W, T, S = BuildRole.WRITER, BuildRole.TESTER, BuildRole.STORE_MANAGER


def test_one_rework_then_green(blueprint, tmp_path, monkeypatch):
    runner, outcome, calls = _run(
        blueprint, tmp_path, monkeypatch, {T: [_fail(T, brief_gates.SUITE_CHECK)]}
    )
    assert outcome.outcome is Outcome.SUCCESS, outcome.detail
    (rework,) = _decisions(runner, DECISION_REWORK)
    assert (rework["gate"], rework["round_gate"], rework["round_build"]) == ("TESTER", 1, 1)
    assert len(calls) == 2 and calls[1]  # the writer got the typed findings


def test_two_writer_gate_reworks_then_clean_tester_and_store(blueprint, tmp_path, monkeypatch):
    check = brief_gates.WRITER_BEHAVIOUR_CHECK
    runner, outcome, calls = _run(
        blueprint,
        tmp_path,
        monkeypatch,
        {W: [_fail(W, check, "F1"), _fail(W, check, "F11")]},
    )
    assert outcome.outcome is Outcome.SUCCESS, outcome.detail
    rounds = [(d["gate"], d["round_gate"]) for d in _decisions(runner, DECISION_REWORK)]
    assert rounds == [("WRITER", 1), ("WRITER", 2)]
    assert len(calls) == 3


def test_one_gates_budget_never_reduces_anothers(blueprint, tmp_path, monkeypatch):
    wc = brief_gates.WRITER_BEHAVIOUR_CHECK
    runner, outcome, _ = _run(
        blueprint,
        tmp_path,
        monkeypatch,
        {
            W: [_fail(W, wc, "F1"), _fail(W, wc, "F11")],
            T: [_fail(T, brief_gates.SUITE_CHECK, "red")],
        },
    )
    # WRITER spent its 2; TESTER still had its own 2 and used 1.
    assert outcome.outcome is Outcome.SUCCESS, outcome.detail
    by_gate = {}
    for d in _decisions(runner, DECISION_REWORK):
        by_gate[d["gate"]] = d["round_gate"]
    assert by_gate == {"WRITER": 2, "TESTER": 1}
    assert _decisions(runner, DECISION_REWORK)[-1]["round_build"] == 3


def test_a_gate_that_spent_its_budget_and_fails_again_stops(blueprint, tmp_path, monkeypatch):
    wc = brief_gates.WRITER_BEHAVIOUR_CHECK
    runner, outcome, _ = _run(
        blueprint,
        tmp_path,
        monkeypatch,
        {W: [_fail(W, wc, "a"), _fail(W, wc, "b"), _fail(W, wc, "c")]},
    )
    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT
    (stop,) = _decisions(runner, DECISION_STOP)
    assert outcome.detail.startswith(f"FAILED(WRITER, {wc}, ")


def test_the_same_failure_twice_stops_at_once_even_at_another_gate(
    blueprint, tmp_path, monkeypatch
):
    # The same check failing the same way: once at the WRITER gate, then at
    # TESTER. Budget remains at both gates -- it still stops.
    shared = brief_gates.SUITE_CHECK
    runner, outcome, calls = _run(
        blueprint,
        tmp_path,
        monkeypatch,
        {W: [_fail(W, shared, "same")], T: [_fail(T, shared, "same")]},
    )
    assert outcome.outcome is Outcome.FAILED_GATE
    assert "SAME_FAILURE_TWICE" in outcome.detail
    assert outcome.detail.startswith(f"FAILED(TESTER, {shared}, ")
    assert len(_decisions(runner, DECISION_REWORK)) == 1
    assert len(calls) == 2


def test_the_build_ceiling_stops(blueprint, tmp_path, monkeypatch):
    wc = brief_gates.WRITER_BEHAVIOUR_CHECK
    # Per-gate budget raised past the ceiling so only the ceiling can stop it.
    shapes = [_fail(W, wc, f"shape{i}") for i in range(REWORK_CEILING + 1)]
    runner, outcome, _ = _run(
        blueprint, tmp_path, monkeypatch, {W: shapes}, max_rework=REWORK_CEILING + 5
    )
    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT
    assert len(_decisions(runner, DECISION_REWORK)) == REWORK_CEILING
    (stop,) = _decisions(runner, DECISION_STOP)
    assert stop["round_build"] == REWORK_CEILING and "ceiling" in stop["reason"]


def test_an_advisory_check_never_dispatches_the_writer(blueprint, tmp_path, monkeypatch):
    runner, outcome, calls = _run(
        blueprint, tmp_path, monkeypatch, {T: [_fail(T, INVENTED)]}
    )
    assert outcome.outcome is Outcome.SUCCESS, outcome.detail
    assert len(calls) == 1
    assert not _decisions(runner, DECISION_REWORK)
    (advisory,) = _decisions(runner, "ADVISORY")
    assert advisory["check"] == INVENTED


def test_a_stop_is_one_terminal_event_with_typed_fields(blueprint, tmp_path, monkeypatch):
    from app.factory.build import rule_decision as rd

    shared = brief_gates.SUITE_CHECK
    runner, outcome, _ = _run(
        blueprint,
        tmp_path,
        monkeypatch,
        {W: [_fail(W, shared, "same")], T: [_fail(T, shared, "same")]},
    )
    stops = [
        e for e in runner.ledger.events()
        if ((e.payload or {}).get(rd.DECISION_KEY) or {}).get(rd.CLASS) == rd.STOP
    ]
    assert len(stops) == 1 and stops[0].kind is EventKind.RUN_FAILED
    assert stops[0].seq == runner.ledger.terminal_event().seq
    rec = rd.stop_record(stops[0])
    for name in (rd.GATE, rd.CHECK, rd.FINDING, rd.CLASS, rd.ROUND_GATE, rd.ROUND_BUILD):
        assert name in rec, name
    assert (rec[rd.GATE], rec[rd.CHECK], rec[rd.ROUND_BUILD]) == ("TESTER", shared, 1)
    assert stopped_by_rule(tmp_path / "build") == rec
    assert stops[0].detail.startswith(rd.stop_status(rec))


def test_resetting_the_budget_gives_every_gate_its_rounds_back(blueprint, tmp_path, monkeypatch):
    from app.factory.build import rule_decision as rd

    wc = brief_gates.WRITER_BEHAVIOUR_CHECK
    runner, outcome, _ = _run(
        blueprint,
        tmp_path,
        monkeypatch,
        {W: [_fail(W, wc, "a"), _fail(W, wc, "b"), _fail(W, wc, "c")]},
    )
    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT
    assert runner._rework_rounds_at(W) == 2

    rd.reset_rework_budget(runner.ledger, reason="Continue")

    assert runner._rework_rounds_at(W) == 0 and runner._rework_rounds_this_build() == 0
    assert runner._last_rework_failures() == []


# -- the N3 Store gate goes through the same rule ----------------------------


def _n3_failure(runner, failed):
    runner.ledger.append(
        EventKind.RUN_FAILED,
        role=S,
        detail="store-gate failed",
        payload={
            "failure_owner": PRODUCT,
            "product_failed": list(failed),
            "product_failed_detail": {c: f"the gate's evidence for {c}" for c in failed},
        },
    )
    return store_gate_verdict(runner.ledger.terminal_event().payload)


def test_a_product_owned_store_gate_failure_reopens_the_writer(blueprint, tmp_path, monkeypatch):
    runner, outcome, _ = _run(blueprint, tmp_path, monkeypatch, {})
    a, b = _product_checks(blueprint)
    verdict = _n3_failure(runner, [a, b])

    decision = runner.reopen_after_store_gate(verdict)

    assert decision.kind == DECISION_REWORK and decision.record["gate"] == "STORE_MANAGER"
    assert any(item.startswith(f"[{a}] Store gate FAIL: the gate's evidence for {a}") for item in decision.work_list)
    assert all(ACCEPTANCE_SELF_CHECK_COMMAND in item for item in decision.work_list)
    assert runner.ledger.terminal_event() is None
    assert not ({W, T, S} & runner.ledger.completed_roles())
    assert runner.ledger.reopening_rework().payload["work_list"] == list(decision.work_list)

    # The re-run hands the reopened WRITER exactly those items.
    seen = []
    roles = {
        role: (lambda ctx, r=role: (seen.append(tuple(ctx.work_list)) if r is W else None)
               or RoleResult(ok=True, detail="stub"))
        for role in BUILD_PHASES
    }
    again = RoleRunner(blueprint, tmp_path / "build", roles=roles, budget=BuildBudget(max_rework=2))
    again.run()
    assert seen and seen[0] == tuple(decision.work_list)


def test_the_same_store_failure_twice_stops_with_the_gate_named(blueprint, tmp_path, monkeypatch):
    runner, _, _ = _run(blueprint, tmp_path, monkeypatch, {})
    a, _ = _product_checks(blueprint)
    assert runner.reopen_after_store_gate(_n3_failure(runner, [a])).kind == DECISION_REWORK
    stop = runner.reopen_after_store_gate(_n3_failure(runner, [a]))
    assert stop.kind == DECISION_STOP
    terminal = runner.ledger.terminal_event()
    assert terminal.kind is EventKind.RUN_FAILED
    assert terminal.detail.startswith(f"FAILED(STORE_MANAGER, {a}, ")


def test_a_factory_owned_store_line_never_becomes_a_verdict():
    assert store_gate_verdict({"failure_owner": "FACTORY", "product_failed": ["x"]}) is None


# -- one acceptance source ----------------------------------------------------


def _harness_text(blueprint) -> str:
    return factory_renders("platform", (), blueprint)[ACCEPTANCE_SCRIPT_REL]


def test_the_writer_starts_with_the_gates_own_harness(blueprint, tmp_path, monkeypatch):
    seen = {}

    def codewhale_writer(ctx):
        script = Path(ctx.workspace.workspace) / ACCEPTANCE_SCRIPT_REL
        seen["text"] = script.read_text(encoding="utf-8") if script.is_file() else None
        return RoleResult(ok=False, reason="stub_writer", location="WRITER", detail="stub")

    monkeypatch.setattr(roles_handlers, "writer_uses_codewhale", lambda env=None: True)
    monkeypatch.setattr(roles_handlers, "_run_writer_via_codewhale_worker", codewhale_writer)
    RoleRunner(blueprint, tmp_path / "build").run()
    assert seen.get("text") == _harness_text(blueprint)


def test_a_writer_trimmed_harness_is_restamped(blueprint, tmp_path):
    script = tmp_path / ACCEPTANCE_SCRIPT_REL
    script.parent.mkdir(parents=True)
    script.write_text("print('ACCEPTANCE: 15/15')\n", encoding="utf-8")
    assert ACCEPTANCE_SCRIPT_REL.as_posix() in refresh_factory_files(tmp_path, "platform", blueprint)
    assert script.read_text(encoding="utf-8") == _harness_text(blueprint)


def _load_harness(blueprint, tmp_path, argv, monkeypatch):
    path = tmp_path / "acceptance.py"
    path.write_text(_harness_text(blueprint), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(path), *argv])
    spec = importlib.util.spec_from_file_location(f"harness_{len(argv)}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gate_only_inputs_skip_with_a_reason_in_a_self_check_and_fail_at_the_gate(
    blueprint, tmp_path, monkeypatch
):
    for var in GATE_ONLY:
        monkeypatch.delenv(var, raising=False)
    local = _load_harness(blueprint, tmp_path / "l", ["--self-check"], monkeypatch) if (tmp_path / "l").mkdir() is None else None
    gate = _load_harness(blueprint, tmp_path / "g", [], monkeypatch) if (tmp_path / "g").mkdir() is None else None
    assert local.SELF_CHECK and not gate.SELF_CHECK
    assert list(local.CHECKS) == list(gate.CHECKS)  # the same roster
    for var in GATE_ONLY:
        status, detail = local._only_the_gate_measures(var)
        assert status == "SKIP" and "measured only by the Store gate" in detail
        assert gate._only_the_gate_measures(var) is None
    # Measured but bad is a FAIL in both.
    monkeypatch.setenv("STORE_DOCKER_HEALTH", "503")
    assert local._only_the_gate_measures("STORE_DOCKER_HEALTH") is None
    assert local.check_docker_health_200(None)[0] == "FAIL"
    monkeypatch.delenv("STORE_DOCKER_HEALTH")
    assert local.check_docker_health_200(None)[0] == "SKIP"
    assert gate.check_docker_health_200(None)[0] == "FAIL"


def test_the_brief_tells_the_writer_to_run_the_harness():
    line = store_gate_acceptance_line()
    assert ACCEPTANCE_SELF_CHECK_COMMAND in line and f"[check:{GATE_NAME}]" in line
