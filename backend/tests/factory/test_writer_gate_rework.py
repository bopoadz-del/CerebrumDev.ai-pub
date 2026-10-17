"""A failed WRITER gate sends the writer back round; it does not end the build.

Live (210d6812, a vineyard brief at production level): every capability
answered ok=true over a failed block (F1). The writer's own suite was green --
it cannot see F1, which needs every block call made to fail -- and the WRITER
gate's failure was terminal, so one variant writer pass killed the export. The
same brief had passed that gate on the previous build.

Two mechanisms, both tested here:

1. A WRITER-gate failure on a check the compiled brief defines is a budgeted
   rework round (the TESTER's rework loop, the same budget and the same
   same-failure-twice rule), with the gate's typed findings in the writer's
   work list. A check the brief never defined goes advisory, as for TESTER.
2. The Factory stamps the gate's own probe into the workspace
   (scripts/factory_checks.py) so the writer can run what the gate runs
   before it declares done; the brief tells it to.

The capability ids below are made up for this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import brief_gates
from app.factory.build import runner as runner_mod
from app.factory.build import ui_e2e, ui_surface, writer_behaviour
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import Outcome, RoleRunner
from tests.factory.test_writer_behaviour_gate import (
    _GUARDED,
    _UNGUARDED,
    _run_gate,
    _write_workspace,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

INVENTED = "zorblat_writer_check"

F1_FINDINGS = [
    "quillfeather_ledger: did not fail closed -- answered {\"ok\": true} "
    "while every block call failed (F1)",
    "quillfeather_ledger: persisted 1 row(s) after a failed handler (F1)",
]


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def _f1_verdict(**payload):
    return GateResult(
        ok=False,
        gate=brief_gates.WRITER_BEHAVIOUR_CHECK,
        reason="writer_behaviour_failed",
        detail=writer_behaviour.F1_HALT,
        findings=list(F1_FINDINGS),
        payload=dict(payload),
    )


def _writer_gate(monkeypatch, verdict, *, times=1):
    """The real gates, except the first ``times`` WRITER verdicts are ``verdict``."""
    real = runner_mod.gate_for
    calls = {"writer": 0}

    def gate_for(role):
        gate = real(role)
        if BuildRole(role) is not BuildRole.WRITER:
            return gate

        def writer_gate(ctx):
            calls["writer"] += 1
            if calls["writer"] <= times:
                return verdict
            return gate(ctx)

        return writer_gate

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    return calls


def _recording_writer(captured):
    real = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        captured.append(tuple(ctx.work_list))
        return real(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    return roles


def _events(runner, kind):
    return [e for e in runner.ledger.events() if e.kind is kind]


# -- the brief defines what the WRITER gate measures --------------------------


def test_every_writer_gate_check_is_one_the_compiled_brief_defines(blueprint):
    from app.factory.blocks_source import resolve_blocks_root
    from app.factory.planner import CapabilityPlanner

    root = resolve_blocks_root()
    root = Path(root) if root else None
    accept = brief_gates.compiled_acceptance_checks(
        blueprint, CapabilityPlanner(root).plan(blueprint), blocks_root=root
    )
    defined = brief_gates.brief_defined_checks(blueprint, accept)

    assert set(brief_gates.WRITER_CHECKS) <= defined
    assert INVENTED not in defined


def test_the_writer_gates_stamp_the_ids_the_brief_renders():
    """One source: each WRITER sub-gate names itself by the constant the
    brief's ACCEPTANCE line is tagged with, so they cannot drift apart."""
    assert writer_behaviour.GATE_NAME == brief_gates.WRITER_BEHAVIOUR_CHECK
    assert ui_surface.GATE_NAME == brief_gates.UI_SURFACE_CHECK
    assert ui_e2e.GATE_NAME == brief_gates.UI_END_TO_END_CHECK
    from app.factory.build.brief_compiler import writer_gate_acceptance_lines

    lines = "\n".join(writer_gate_acceptance_lines())
    for check in brief_gates.WRITER_CHECKS:
        assert f"[check:{check}]" in lines
    # the brief tells the writer to run the gate's own probe
    assert writer_behaviour.SELF_CHECK_COMMAND in lines


# -- mechanism 1: a failed WRITER gate is a rework round ----------------------


def test_a_failed_writer_gate_sends_the_writer_back_with_its_typed_findings(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    calls = _writer_gate(monkeypatch, _f1_verdict())
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 1
    assert calls["writer"] == 2, "the WRITER gate judged the reworked pass too"

    reworks = _events(runner, EventKind.REWORK)
    assert len(reworks) == 1
    assert reworks[0].role is BuildRole.WRITER
    assert reworks[0].payload["source"] == BuildRole.WRITER.value
    assert reworks[0].payload["gate"] == brief_gates.WRITER_BEHAVIOUR_CHECK
    assert reworks[0].payload["findings"] == F1_FINDINGS

    # first pass: no work list; the rework pass: every finding, named by its
    # check, then the command that runs the gate's own probe
    assert captured[0] == ()
    work = captured[1]
    for finding in F1_FINDINGS:
        assert f"[{brief_gates.WRITER_BEHAVIOUR_CHECK}] {finding}" in work
    assert any(writer_behaviour.SELF_CHECK_COMMAND in item for item in work)

    # the build carried on past WRITER: TESTER ran after the reworked pass
    events = list(runner.ledger.events())
    rework_at = events.index(reworks[0])
    assert [
        e for e in events[rework_at:]
        if e.kind is EventKind.PHASE_STARTED and e.role is BuildRole.TESTER
    ], "TESTER ran after the WRITER rework"


def test_the_same_writer_gate_failure_twice_stops_the_run(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _writer_gate(monkeypatch, _f1_verdict(), times=99)
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert not outcome.ok
    assert outcome.outcome is Outcome.FAILED_GATE
    # The owner's stop status: FAILED(<gate>, <check>, <finding>): <reason>.
    assert outcome.detail.startswith(
        f"FAILED(WRITER, {brief_gates.WRITER_BEHAVIOUR_CHECK}, "
    ), outcome.detail
    assert "SAME_FAILURE_TWICE" in outcome.detail, outcome.detail
    assert outcome.rework_used == 1
    assert len(captured) == 2, "one first pass, one rework -- never a third"


def test_a_writer_gate_failure_the_brief_never_defined_goes_advisory(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _writer_gate(monkeypatch, _f1_verdict(check=INVENTED))
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 0
    assert _events(runner, EventKind.REWORK) == []
    assert len(captured) == 1, "no writer round on a check the brief never defined"
    note = next(
        e for e in _events(runner, EventKind.NOTE) if (e.payload or {}).get("gate_advisory")
    )
    assert note.role is BuildRole.WRITER
    assert [a["check"] for a in note.payload["gate_advisory"]] == [INVENTED]


# -- mechanism 2: the writer can run the gate's own probe ---------------------


def _self_check(workspace: Path) -> subprocess.CompletedProcess:
    writer_behaviour.emit_self_check(workspace)
    return subprocess.run(
        [sys.executable, writer_behaviour.SELF_CHECK_REL],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=900,
    )


def _printed(out: str, levels) -> list:
    """The record texts the self-check printed at ``levels``."""
    texts = []
    for line in out.splitlines():
        for level in levels:
            head = f"[{level}] "
            if line.startswith(head):
                texts.append(line[len(head):].split(": ", 1)[1])
    return texts


def test_the_self_check_prints_exactly_what_the_gate_records(tmp_path):
    _write_workspace(tmp_path, _UNGUARDED)
    gate = _run_gate(tmp_path)
    assert gate.ok is False and gate.gate == brief_gates.WRITER_BEHAVIOUR_CHECK

    proc = _self_check(tmp_path)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "WRITER GATE WOULD FAIL" in proc.stdout
    assert _printed(proc.stdout, ("halt", "finding"))[-20:] == list(gate.findings)


def test_the_self_check_is_clean_when_the_gate_passes(tmp_path):
    _write_workspace(tmp_path, _GUARDED)
    gate = _run_gate(tmp_path)
    assert gate.ok is True, gate.findings

    proc = _self_check(tmp_path)

    assert proc.returncode == (1 if gate.findings else 0), proc.stdout + proc.stderr
    assert _printed(proc.stdout, ("miss",)) == list(gate.findings)


def test_the_stamped_self_check_is_the_gates_own_probe():
    """No copy drift: the stamp embeds the probe the gate renders, byte for byte."""
    assert repr(writer_behaviour._render_probe()) in writer_behaviour.render_self_check()


def test_the_writer_pass_ships_the_self_check(blueprint, tmp_path, stub_coder):
    runner = RoleRunner(blueprint, tmp_path / "build")
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    stamped = tmp_path / "build" / writer_behaviour.SELF_CHECK_REL
    assert stamped.is_file()
    # The stamp is the Factory's bytes alone (its payload base is read off the
    # live models when it runs), so a refresh never sees a product-made change.
    assert stamped.read_text(encoding="utf-8") == writer_behaviour.render_self_check()
