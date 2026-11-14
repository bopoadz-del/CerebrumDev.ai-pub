"""F-U1: the owner's Stop really stops the build in flight.

14 Nov: the console's Stop / Pause wrote ``docs/coder_control.json`` and
nothing running read it -- the only reader was the retired coder-session
dispatcher. The writer CLI's wait loop, the HTTP model-call watchdog and the
phase loop never looked, so a stopped build kept its CLI (and its model
calls) alive, kept its slot, and a cancelled pilot had no way to reach it.

Each test runs the stopped case and the not-stopped case.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from app.factory import llm_watchdog
from app.factory.blueprint import load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import EventKind
from app.factory.build.coder_session_status import (
    OwnerStopped,
    owner_control_scope,
    read_control_record,
    write_control,
)
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.rule_decision import DECISION_KEY
from app.factory.build.runner import BUILD_PHASES, Outcome, RoleRunner
from app.factory.build.tenant_bind import bind_tenant_store

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")

sys.path.insert(0, str(ROOT / "scripts"))


def _alive(pid: int) -> bool:
    """A process that exists and is not a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _fake_agent(tmp_path: Path, ticks: int) -> Path:
    """A CLI that starts a child, then 'calls the model' once per tick."""
    script = tmp_path / "fake_agent.sh"
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo 1.0; exit 0; fi\n'
        f"sleep 300 >/dev/null 2>&1 &\necho $! > {out}/child.pid\n"
        f"i=0\nwhile [ $i -lt {ticks} ]; do echo call >> {out}/calls; i=$((i+1)); sleep 0.2; done\n"
        f"echo done > {out}/done\n"
        "echo '{\"status\": \"completed\"}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _calls(tmp_path: Path) -> int:
    try:
        return len((tmp_path / "out" / "calls").read_text().splitlines())
    except OSError:
        return 0


def _run_job(tmp_path: Path, agent: Path, control, timeout_s: float = 60) -> dict:
    result: dict = {}
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value=str(agent)), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"):
        started = time.monotonic()
        try:
            result["receipt"] = codewhale_worker.run_worker_job(
                "write the platform",
                tmp_path / "checkout",
                tenant_store=bind_tenant_store("acct-stop"),
                progress=lambda line, info: None,
                timeout_s=timeout_s,
                control=control,
            )
        except OwnerStopped as exc:
            result["stopped"] = exc
        except Exception as exc:  # noqa: BLE001 -- recorded for the assertion
            result["error"] = exc
        result["wall"] = time.monotonic() - started
    return result


# --- (a) + (b): Stop kills the CLI and its children; the slot is free -------


@POSIX_ONLY
@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_stop_kills_the_writer_tree_and_frees_the_slot(tmp_path, pressed, stopped):
    agent = _fake_agent(tmp_path, ticks=10)
    child_pid_file = tmp_path / "out" / "child.pid"

    def control() -> str:
        # The owner presses once the CLI is working (its first model call).
        return pressed if _calls(tmp_path) >= 1 else "run"

    result = _run_job(tmp_path, agent, control)
    assert ("stopped" in result) == stopped, result
    assert (tmp_path / "out" / "done").exists() == (not stopped)
    child = int(child_pid_file.read_text().strip())
    if stopped:
        assert result["wall"] < 10, "a stop ends the job within seconds"
        calls_at_stop = _calls(tmp_path)
        time.sleep(1.0)
        assert _calls(tmp_path) == calls_at_stop, "no model call after the stop"
        deadline = time.monotonic() + 5
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(child), "the CLI's child outlived the stop"
    else:
        os.kill(child, 9)
    snap = codewhale_worker.slot_counter().snapshot()
    assert snap["total"] == 0 and snap["by_tenant"] == {}, snap


# --- (d): Pause freezes the CLI; Resume continues from where it was ---------


@POSIX_ONLY
@pytest.mark.parametrize("pressed, paused", [("pause", True), ("run", False)])
def test_pause_freezes_the_writer_and_resume_continues(tmp_path, pressed, paused):
    ticks = 12
    agent = _fake_agent(tmp_path, ticks=ticks)
    box = {"action": "run"}
    holder: dict = {}
    job = threading.Thread(
        # A 4s wall: a ~3s pause on top of a ~2.5s job only fits if the
        # paused time is not counted against the writer.
        target=lambda: holder.update(_run_job(tmp_path, agent, lambda: box["action"], timeout_s=4)),
        daemon=True,
    )
    job.start()
    deadline = time.monotonic() + 10
    while _calls(tmp_path) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    box["action"] = pressed
    time.sleep(0.8)
    before = _calls(tmp_path)
    time.sleep(2.0)
    grew = _calls(tmp_path) - before
    assert (grew == 0) == paused, f"{grew} model call(s) while {pressed}"
    box["action"] = "run"
    job.join(timeout=30)
    assert "receipt" in holder, holder
    assert _calls(tmp_path) == ticks, "resume continues, it never restarts"
    os.kill(int((tmp_path / "out" / "child.pid").read_text().strip()), 9)


# --- (c): Stop mid model call (the HTTP stages) -------------------------------


@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_stop_cancels_a_model_call_in_flight(pressed, stopped):
    release = threading.Event()
    answer = object()

    def slow_post(url, **_kw):
        release.wait(2.0)
        return answer

    pressed_at = time.monotonic() + 0.3

    def control() -> str:
        return pressed if time.monotonic() >= pressed_at else "run"

    started = time.monotonic()
    try:
        with owner_control_scope(control):
            got = llm_watchdog.post_with_deadline(
                "http://model.invalid/v1", json={}, post=slow_post, timeout=30
            )
        outcome = "answered" if got is answer else "other"
    except OwnerStopped:
        outcome = "stopped"
    finally:
        release.set()
    assert (outcome == "stopped") == stopped, outcome
    if stopped:
        assert time.monotonic() - started < 1.5, "the call is cancelled, not waited for"


# --- (b) + (c) + (f): every stage ends as one named final state, in-process --


@pytest.fixture()
def _every_phase_runs(monkeypatch):
    """No paid calls, and every phase runs (collect-all: a failed gate is a
    finding, not a halt), so a stop can be pressed at any stage. A stop is
    not a finding: collect-all never sees it."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_GATE_COLLECT_ALL", "1")


def _phases_started(runner) -> list:
    return [e.role for e in runner.ledger.events() if e.kind is EventKind.PHASE_STARTED]


@pytest.mark.parametrize("stage", [*BUILD_PHASES, None])
def test_stop_at_any_stage_ends_the_build_as_stopped_by_owner(
    tmp_path, monkeypatch, stub_coder, _every_phase_runs, stage
):
    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)

    def pressing_stop(inner):
        def role(ctx):
            write_control(out, "stop", by="owner-account")
            # The stage's next model call is where the stop lands.
            llm_watchdog.post_with_deadline(
                "http://model.invalid/v1", json={}, post=lambda *a, **k: None, timeout=5
            )
            return inner(ctx)

        return role

    if stage is not None:
        roles[stage] = pressing_stop(roles[stage])
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles)
    outcome = runner.run()

    assert (outcome.outcome is Outcome.STOPPED_BY_OWNER) == (stage is not None)
    if stage is None:
        assert _phases_started(runner) == list(BUILD_PHASES), "an unstopped build runs on"
        return
    assert _phases_started(runner)[-1] is stage, "nothing ran after the stop"
    assert outcome.failed_phase is stage
    decision = runner.ledger.terminal_event().payload[DECISION_KEY]
    assert decision["reason"] == "stopped_by_owner"
    assert decision["by"] == "owner-account"
    assert decision["at"] == read_control_record(out)["updated_at"]


# --- (d) between stages: no phase starts while paused -------------------------


@pytest.mark.parametrize("pressed, held", [("pause", True), ("run", False)])
def test_no_phase_starts_while_paused(
    tmp_path, monkeypatch, stub_coder, _every_phase_runs, pressed, held
):
    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)
    started: list = []
    collector, cloner = roles[BuildRole.COLLECTOR], roles[BuildRole.CLONER]

    def pressing(ctx):
        write_control(out, pressed)
        return collector(ctx)

    def watched_cloner(ctx):
        started.append(time.monotonic())
        return cloner(ctx)

    roles[BuildRole.COLLECTOR] = pressing
    roles[BuildRole.CLONER] = watched_cloner
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles)
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(o=runner.run()), daemon=True)
    thread.start()
    time.sleep(1.5)
    assert (not started) == held, "a phase started while the owner had paused"
    write_control(out, "resume")
    thread.join(timeout=120)
    assert result["o"].outcome is not Outcome.STOPPED_BY_OWNER
    assert _phases_started(runner) == list(BUILD_PHASES), "resume continues the build"


# --- (e): a cancelled pilot stops its own build through the one route -------


@pytest.mark.parametrize("sid, sent", [("sess-pilot", True), (None, False)])
def test_a_cancelled_pilot_stops_its_own_build(sid, sent):
    import pilot_runner

    calls: list = []

    def fake_req(method, path, body=None, token=None, **_kw):
        calls.append((method, path, body, token))
        return 200, {"ok": True}

    assert pilot_runner.stop_own_build(sid, "tok", req=fake_req) == sent
    expected = (
        [("POST", f"/v1/sessions/{sid}/product/coder-control", {"action": "stop"}, "tok")]
        if sent
        else []
    )
    assert calls == expected


@pytest.mark.parametrize("by", ["acct-owner", None])
def test_the_stop_record_says_who_pressed_it(tmp_path, by):
    write_control(tmp_path, "stop", by=by)
    record = read_control_record(tmp_path)
    assert record["action"] == "stop"
    assert record.get("by") == by
