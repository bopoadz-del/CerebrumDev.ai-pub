"""Straight-through pipeline: CLONER -> WRITER with no pause; TESTER before N3."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateResult, gate_for as real_gate_for
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleError, RoleResult
from app.factory.build.runner import Outcome, RoleRunner, blueprint_hash
from app.factory.build_jobs import build_status

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _bp():
    return load_blueprint(SMOKE)


def _seed_cloner_done(out: Path, bp) -> BuildLedger:
    digest = blueprint_hash(bp)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=digest)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(
            EventKind.GATE_PASSED,
            role=role,
            detail="ok",
            payload={"gate": "seed"},
        )
    return ledger


def _pass_later_gates(role):
    if role in {BuildRole.TESTER, BuildRole.STORE_MANAGER}:
        return lambda _ctx: GateResult(ok=True, gate=f"{role.value}_stub", detail="ok")
    return real_gate_for(role)


def test_cloner_pass_runs_writer_straight_through(tmp_path):
    """A passing CLONER gate must NOT end the run.

    The live loop is COLLECTOR -> CLONER -> WRITER -> TESTER ->
    STORE_MANAGER with no post-Cloner pause and no launch action: WRITER is
    reached on the same run, without a Continue.
    """
    writer_calls: list[bool] = []

    def writer(_ctx):
        writer_calls.append(True)
        raise RoleError("stopped inside WRITER - reached with no hold")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    out = tmp_path / "build"
    outcome = RoleRunner(_bp(), out, roles=roles).run()

    # WRITER ran in the same pass that finished CLONER.
    assert writer_calls == [True]
    assert BuildRole.CLONER in outcome.completed
    # Our stub writer raised; the run is a real role error, never a pause.
    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR

    started = [
        e.role
        for e in BuildLedger(out / "build_ledger.jsonl").events()
        if e.kind is EventKind.PHASE_STARTED
    ]
    assert BuildRole.WRITER in started


def test_build_status_can_never_park_a_run_as_waiting(tmp_path):
    """No code path may report a build as parked after CLONER."""
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = lambda _ctx: RoleResult(ok=True, detail="writer ran")
    out = tmp_path / "build"
    RoleRunner(_bp(), out, roles=roles).run()

    status = build_status(out)
    assert status["state"] != "waiting"
    assert [k for k in status if k.startswith("awaiting_")] == []
    assert "AWAITING" not in str(status.get("outcome") or "")


def test_docker_unavailable_hands_off_to_n3(monkeypatch, tmp_path):
    """A docker-less host must not fake Store-green and must not just fail:
    with cerebrum-builds armed, the workspace hands off to the N3 store-gate."""
    import app.factory.build.runner as runner_mod
    from app.factory.build.gates import GateResult

    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "tok")
    pushed = []
    dispatched = []

    def fake_push(workspace, *, env, session_id, run_git=None, suffix=None):
        pushed.append(str(workspace))
        return type("R", (), {"branch": "build/x", "sha": "abc"})()

    def fake_dispatch(branch, *, env=None, opener=None):
        dispatched.append(branch)

    monkeypatch.setattr(
        "app.factory.build.builds_push.push_workspace", fake_push
    )
    monkeypatch.setattr(
        "app.factory.build.n3_store_gate.dispatch_store_gate", fake_dispatch
    )

    def fake_gate(role):
        if role is BuildRole.STORE_MANAGER:
            return lambda _ctx: GateResult(
                ok=False,
                gate="store_manager_contract",
                reason="docker_unavailable",
                detail="STORE (acceptance): docker is not available; will not pass on a host-side skip",
            )
        return lambda _ctx: GateResult(ok=True, gate=f"{role.value}_stub", detail="ok")

    monkeypatch.setattr(runner_mod, "gate_for", fake_gate)
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = lambda _ctx: RoleResult(ok=True, detail="writer ran")

    out = tmp_path / "build"
    outcome = RoleRunner(_bp(), out, roles=roles).run()

    assert outcome.outcome is Outcome.HANDOFF_TO_N3, outcome
    assert pushed, "the workspace was never pushed to cerebrum-builds"
    assert dispatched == ["build/x"], "the store-gate workflow was not dispatched"
    notes = [
        e
        for e in BuildLedger(out / "build_ledger.jsonl").events()
        if e.kind is EventKind.NOTE
    ]
    assert any("cerebrum-builds" in (e.detail or "") for e in notes)


def test_docker_unavailable_without_n3_armed_stays_a_named_gate_failure(
    monkeypatch, tmp_path,
):
    """No builds token: the docker refusal stays exactly what it was —
    a named gate failure, never a handoff that cannot complete."""
    import app.factory.build.runner as runner_mod
    from app.factory.build.gates import GateResult

    monkeypatch.delenv("CEREBRUM_BUILDS_GITHUB_TOKEN", raising=False)

    def fake_gate(role):
        if role is BuildRole.STORE_MANAGER:
            return lambda _ctx: GateResult(
                ok=False,
                gate="store_manager_contract",
                reason="docker_unavailable",
                detail="STORE (acceptance): docker is not available",
            )
        return lambda _ctx: GateResult(ok=True, gate=f"{role.value}_stub", detail="ok")

    monkeypatch.setattr(runner_mod, "gate_for", fake_gate)
    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = lambda _ctx: RoleResult(ok=True, detail="writer ran")

    out = tmp_path / "build"
    outcome = RoleRunner(_bp(), out, roles=roles).run()

    assert outcome.outcome is Outcome.FAILED_GATE, outcome
    assert "docker" in outcome.detail
