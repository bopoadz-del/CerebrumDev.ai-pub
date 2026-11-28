"""Two different WRITER-gate failures are two failures, not one failure twice.

Live (cycle 4 smoke B, sess_f47605b0e22b4828, 55d91d62): round 1 of the WRITER
gate failed ``writer_contract`` with reason ``writer_authored_factory_file``;
the writer was reworked for that. Round 2 failed ``writer_contract`` with a
different reason, ``locked_block_loaded_elsewhere`` -- and the runner stopped
the build on SAME_FAILURE_TWICE, because a verdict that declares no
``finding_shape`` was keyed ``writer_contract:failed`` whatever its reason.
The writer never heard the second finding.

The key is check + finding shape; a gate's typed reason IS its finding shape
when it declares none (gates.GateResult already types each finding that way).

The capability ids and reasons below are made up for this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import brief_gates
from app.factory.build import runner as runner_mod
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import REWORK_BUDGET, Outcome, RoleRunner, _failure_keys

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _verdict(reason: str, finding: str, **payload) -> GateResult:
    return GateResult(
        ok=False,
        gate=brief_gates.WRITER_CONTRACT_CHECK,
        reason=reason,
        detail=f"{reason}: {finding}",
        findings=[finding],
        payload=dict(payload),
    )


FIRST = _verdict("zorblat_first_reason", "quillfeather: the first thing wrong")
SECOND = _verdict("zorblat_second_reason", "quillfeather: a different thing wrong")


def test_two_reasons_of_one_check_are_two_keys():
    assert set(_failure_keys(FIRST)).isdisjoint(_failure_keys(SECOND))


def test_one_reason_twice_is_one_key():
    again = _verdict("zorblat_first_reason", "quillfeather: worded differently this round")
    assert _failure_keys(FIRST) == _failure_keys(again)


def test_a_declared_finding_shape_still_wins_over_the_reason():
    a = _verdict("zorblat_first_reason", "x", finding_shape="zorblat_shape")
    b = _verdict("zorblat_second_reason", "y", finding_shape="zorblat_shape")
    assert _failure_keys(a) == _failure_keys(b)


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _writer_gate_sequence(monkeypatch, verdicts):
    """The real gates, except the WRITER answers ``verdicts`` in order first."""
    real = runner_mod.gate_for
    queue = list(verdicts)

    def gate_for(role):
        gate = real(role)
        if BuildRole(role) is not BuildRole.WRITER:
            return gate

        def writer_gate(ctx):
            return queue.pop(0) if queue else gate(ctx)

        return writer_gate

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)


def _recording_writer(captured):
    real = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        captured.append(tuple(ctx.work_list))
        return real(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    return roles


def test_a_different_second_failure_gets_its_own_rework_round(tmp_path, stub_coder, monkeypatch):
    """Each DIFFERENT failure gets its own rework round, up to REWORK_BUDGET
    per gate; the failure after the last round stops with ITS finding named --
    never a SAME_FAILURE_TWICE that hides what the writer must hear next."""
    distinct = [FIRST, SECOND] + [
        _verdict(f"zorblat_extra_reason_{n}", f"quillfeather: extra thing wrong number {n}")
        for n in range(REWORK_BUDGET - 1)
    ]
    _writer_gate_sequence(monkeypatch, distinct)
    captured: list = []
    runner = RoleRunner(load_blueprint(SMOKE), tmp_path / "build", roles=_recording_writer(captured), brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}')
    outcome = runner.run()

    assert "SAME_FAILURE_TWICE" not in (outcome.detail or ""), outcome.detail
    reworks = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert len(reworks) == REWORK_BUDGET, [e.detail for e in reworks]
    # Round 1 handed the writer the FIRST finding, round 2 the SECOND...
    assert any("the first thing wrong" in item for item in captured[1]), captured
    assert any("a different thing wrong" in item for item in captured[2]), captured
    # ...and the stop names the failure after the last round, so the next build hears it.
    last = distinct[-1].findings[0].split(": ", 1)[1]
    assert last in (outcome.detail or ""), outcome.detail
    assert outcome.outcome is Outcome.FAILED_BUDGET_SPENT


def test_the_same_failure_twice_still_stops(tmp_path, stub_coder, monkeypatch):
    again = _verdict("zorblat_first_reason", "quillfeather: the first thing wrong")
    _writer_gate_sequence(monkeypatch, [FIRST, again])
    runner = RoleRunner(load_blueprint(SMOKE), tmp_path / "build", roles=_recording_writer([]), brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}')
    outcome = runner.run()

    assert not outcome.ok
    assert outcome.outcome is Outcome.FAILED_GATE
    assert "SAME_FAILURE_TWICE" in outcome.detail, outcome.detail
