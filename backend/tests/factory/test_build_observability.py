"""A long build must be observable, and a dead build must not look alive.

New-shape tests for two defects found while watching the FIRST live runner
build on production:

1. The ledger only records phase boundaries, so a WRITER pass of ~16 agent
   calls reported a frozen "2/5" for twenty-plus minutes. A customer -- and
   an operator debugging it -- could not distinguish work from a hang.
   Worse, nothing was in the logs either: no logging was configured, so
   every ``logging.getLogger("cerebrumdev...")`` line was dropped while
   uvicorn's access logs printed. The build was completely unobservable.

2. A build whose thread dies (worker restart, redeploy, OOM) leaves the
   ledger's last event as PHASE_STARTED forever, which read as "building"
   for eternity -- an infinite spinner for a build that no longer exists.

Progress is recorded as NOTE events, which must NOT disturb the verdict
readers: a progress line can never be mistaken for a gate result.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import RoleContext
from app.factory.build.runner import RoleRunner
from app.factory.build_jobs import build_status

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_note_is_a_noop_without_a_progress_sink():
    """Roles stay testable without a ledger, and telemetry never fails a
    build: a raising sink is swallowed."""
    ctx = RoleContext(role=BuildRole.WRITER, workspace=None, blueprint=None, plan=None)
    ctx.note("nothing is listening")  # must not raise

    def _boom(detail, payload):
        raise RuntimeError("telemetry backend down")

    ctx.progress = _boom
    ctx.note("still must not raise")


def test_a_build_records_intra_phase_progress(tmp_path, stub_coder):
    """The WRITER must say what it is doing while it does it."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    assert runner.run().ok

    notes = [e for e in runner.ledger.events() if e.kind is EventKind.NOTE]
    assert notes, "the build reported no intra-phase progress at all"

    stages = {(n.payload or {}).get("stage") for n in notes}
    assert "handlers" in stages, stages
    assert "routes" in stages, stages
    # Every progress line must be attributable and countable, or a UI cannot
    # render it as progress.
    for note in notes:
        assert note.role is not None
        assert note.detail
        payload = note.payload or {}
        if payload.get("stage") in ("handlers", "routes"):
            assert isinstance(payload.get("done"), int)
            assert isinstance(payload.get("total"), int)


def test_progress_notes_do_not_disturb_the_verdict_readers(tmp_path, stub_coder):
    """NOTE is not a verdict. completed_roles / terminal_event / succeeded
    must read exactly as they did before progress existed."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    outcome = runner.run()
    assert outcome.ok

    ledger = BuildLedger(out / "build_ledger.jsonl")
    assert ledger.succeeded()
    assert ledger.terminal_event().kind is EventKind.RUN_SUCCEEDED
    assert len(ledger.completed_roles()) == 5
    # And a killed-phase reader must not treat a NOTE as an unfinished phase.
    assert ledger.interrupted_role() is None


def test_status_surfaces_the_current_activity(tmp_path):
    """What the client polls while waiting."""
    out = tmp_path / "build"
    ledger = BuildLedger(out / "build_ledger.jsonl")
    out.mkdir(parents=True, exist_ok=True)
    ledger.start_run(product_id="probe", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler defect_register (coder LLM (kimi-k2.7-code))",
        payload={"stage": "handlers", "done": 2, "total": 5},
    )

    status = build_status(out)
    assert status["state"] == "building"
    assert "defect_register" in status["activity"]
    assert status["activity_stage"] == "handlers"
    assert status["activity_done"] == 2
    assert status["activity_total"] == 5
    assert status["current_phase"] == {
        "id": "WRITER",
        "label": "Platform manufacturer",
    }
    assert status["phase_index"] == 3
    assert status["phase_total"] == 5
    assert status["phases_done"] == 0
    assert status["next_phase"] == {"id": "TESTER", "label": "Acceptance inspector"}
    assert status["phase_progress"] == {
        "done": 2,
        "total": 5,
        "fraction": 0.4,
        "stage": "handlers",
    }
    assert "defect_register" in status["last_event"]
    assert status["last_event_at"]
    assert status["stale"] is False


def test_status_names_cloner_and_marks_a_quiet_build_stale(tmp_path, monkeypatch):
    """2/5 on the Floor is completed phases. The customer needs the current
    name (CLONER) plus whether the last event is recent or the job went quiet.
    """
    out = tmp_path / "build"
    ledger = BuildLedger(out / "build_ledger.jsonl")
    out.mkdir(parents=True, exist_ok=True)
    ledger.start_run(product_id="probe", inputs_hash="abc")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.COLLECTOR, detail="COLLECTOR")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.CLONER, detail="CLONER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.CLONER,
        detail="cloned audit",
        payload={"stage": "blocks", "done": 3, "total": 7},
    )

    status = build_status(out)
    assert status["state"] == "building"
    assert status["current_phase"]["id"] == "CLONER"
    assert status["phase_index"] == 2
    assert status["phases_done"] == 1
    assert status["next_phase"]["id"] == "WRITER"
    assert status["phase_progress"]["done"] == 3
    assert status["phase_progress"]["total"] == 7
    assert status["last_event"] == "cloned audit"
    assert status["stale"] is False

    from app.factory import build_jobs
    from app.factory.build_jobs import _STALE_AFTER_S

    # "Quiet" is only a building state while a thread is behind it; with no
    # thread the same silence is an orphan (covered below).
    monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda product_id: True)

    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=_STALE_AFTER_S + 20)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    quiet = build_status(out)
    assert quiet["state"] == "building", quiet
    assert quiet["stale"] is True
    assert quiet["current_phase"]["id"] == "CLONER"
    assert quiet["last_event_age_s"] >= _STALE_AFTER_S


def test_a_build_with_no_process_behind_it_reports_stalled(tmp_path):
    """A redeploy kills the build thread. The ledger's last event stays
    PHASE_STARTED, which used to read as "building" forever -- an infinite
    spinner for a build that cannot finish."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="probe", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")

    assert build_status(out)["state"] == "building"

    # Age the ledger past the stall threshold, exactly as a dead build looks.
    from app.factory.build_jobs import _STALL_AFTER_S

    old = time.time() - (_STALL_AFTER_S + 120)
    os.utime(out / "build_ledger.jsonl", (old, old))

    status = build_status(out)
    assert status["state"] == "stalled", status
    # No thread in this process: named as an orphan, with the way out.
    assert "continue" in status["detail"]
    assert status["honesty"] == "BUILD_THREAD_ORPHANED"
    # A stalled build is not downloadable either.
    from app.factory.build_jobs import is_build_complete

    assert not is_build_complete(out)


def test_application_logs_reach_stdout():
    """The operability half: app loggers had no handler in production, so a
    long build emitted nothing diagnosable.

    Asserted on the configured logging tree rather than captured output --
    pytest installs its own capture handlers, which would pass even with no
    application handler at all (a false green about the very thing under
    test)."""
    import logging

    from app.main import _configure_logging

    _configure_logging()
    root = logging.getLogger()
    assert root.level <= logging.INFO, logging.getLevelName(root.level)
    stdout_handlers = [
        h for h in root.handlers if getattr(h, "_cerebrum_stdout", False)
    ]
    assert stdout_handlers, "no application stdout handler was installed"
    assert stdout_handlers[0].stream is sys.stdout

    # And an app logger must actually be enabled for INFO through that tree.
    assert logging.getLogger("cerebrumdev.factory.probe").isEnabledFor(logging.INFO)

    # Idempotent: repeated calls (reload, import cycles) must not stack
    # handlers and print every line N times.
    _configure_logging()
    assert len(
        [h for h in logging.getLogger().handlers if getattr(h, "_cerebrum_stdout", False)]
    ) == 1


def test_the_coder_yields_when_the_build_budget_is_nearly_spent(tmp_path):
    """The wall clock is only checked BETWEEN phases, so a slow model can run
    the WRITER unbounded -- a production build sat in it for 39 minutes.

    With little budget left the coder must not start another call: it hands
    the artifact to the deterministic template and RECORDS the skip, so the
    build finishes honestly instead of hanging.
    """
    import time as _time

    from app.factory.build.roles import _budget_too_low

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=None,
        plan=None,
        deadline=_time.monotonic() + 5,  # 5s left: no call can finish
    )
    assert _budget_too_low(ctx, "handler") is True
    failures = ctx.state["coder_failures"]
    assert "handler" in failures
    assert "budget" in failures["handler"]

    # Plenty of budget: the coder runs normally.
    roomy = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=None,
        plan=None,
        deadline=_time.monotonic() + 3600,
    )
    assert _budget_too_low(roomy, "handler") is False
    assert not roomy.state.get("coder_failures")

    # No deadline configured means unbounded, as before.
    unbounded = RoleContext(
        role=BuildRole.WRITER, workspace=None, blueprint=None, plan=None
    )
    assert _budget_too_low(unbounded, "handler") is False


def test_a_read_timeout_is_not_retried(monkeypatch):
    """Retrying a read timeout cost the same wait again for the same likely
    outcome: 3 attempts x 2 model legs x 180s = up to 18 minutes for ONE
    artifact, which is what stalled the first production build. Connection
    failures stay retryable."""
    import httpx

    from app.factory import coder

    calls = []

    def _timeout(url, json=None, headers=None, timeout=None):
        calls.append(timeout)
        raise httpx.ReadTimeout("model did not answer")

    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "30")
    monkeypatch.setattr(coder.httpx, "post", _timeout)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {
            "provider": "kimi",
            "model": "primary",
            "fallback_model": "fallback",
            "base_url": "https://api.moonshot.ai/v1",
            "api_key": "test-key-not-real",
        },
    )

    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])

    assert "coder LLM timed out" in str(exc.value)
    # One attempt per model leg, not three.
    assert len(calls) == 2, calls
    # And the per-call wait is bounded and configurable.
    assert all(t <= 180 for t in calls), calls


def test_a_missing_test_runner_is_not_reported_as_failing_tests(tmp_path):
    """THE defect that made every production build fail at 3/5.

    pytest lived only in requirements-dev.txt while the production image
    installs requirements.txt, so the TESTER gate's subprocess died with
    "No module named pytest". Its stderr starts with none of FAILED/ERROR/E,
    so the gate reported detail "suite is red" with ZERO findings -- and the
    rework loop sent the agent back three times to rewrite handlers that
    were never the problem. A build-environment fault must say so.
    """
    import subprocess

    from app.factory.build.gates import GateContext, gate_suite_green

    tests_dir = tmp_path / "tests"
    (tmp_path / "app").mkdir()  # suite gate requires the app package first
    tests_dir.mkdir()
    (tests_dir / "test_probe.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")

    def _no_pytest(argv, cwd=None, timeout=None):
        return subprocess.CompletedProcess(
            argv, 1, "", "C:\python.exe: No module named pytest\n"
        )

    result = gate_suite_green(
        GateContext(workspace=tmp_path, role=BuildRole.TESTER, runner=_no_pytest)
    )
    assert result.ok is False
    assert "could not be RUN" in result.detail, result.detail
    assert result.payload.get("infrastructure") is True
    # And it must never be actionless: the agent cannot fix an empty finding.
    assert result.findings
    assert any("No module named pytest" in f for f in result.findings)


def test_a_genuinely_red_suite_still_reports_red_with_findings(tmp_path):
    """The infrastructure branch must not swallow real test failures."""
    import subprocess

    from app.factory.build.gates import GateContext, gate_suite_green

    tests_dir = tmp_path / "tests"
    (tmp_path / "app").mkdir()  # suite gate requires the app package first
    tests_dir.mkdir()
    (tests_dir / "test_probe.py").write_text("def test_x():\n    assert 0\n", encoding="utf-8")

    def _red(argv, cwd=None, timeout=None):
        return subprocess.CompletedProcess(
            argv,
            1,
            "FAILED tests/test_probe.py::test_x - assert 0\n1 failed in 0.1s\n",
            "",
        )

    result = gate_suite_green(
        GateContext(workspace=tmp_path, role=BuildRole.TESTER, runner=_red)
    )
    assert result.ok is False
    assert result.detail.startswith("suite is red")
    assert "FAILED tests/test_probe.py::test_x" in result.detail
    assert not result.payload.get("infrastructure")
    assert any("FAILED" in f for f in result.findings)


def test_a_red_suite_never_reports_zero_findings(tmp_path):
    """A failure with nothing to act on burned three rework rounds. Even
    unparseable output must yield something concrete."""
    import subprocess

    from app.factory.build.gates import GateContext, gate_suite_green

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_probe.py").write_text("def test_x():\n    pass\n", encoding="utf-8")

    def _weird(argv, cwd=None, timeout=None):
        return subprocess.CompletedProcess(argv, 2, "collected 1 item\nsomething odd\n", "")

    result = gate_suite_green(
        GateContext(workspace=tmp_path, role=BuildRole.TESTER, runner=_weird)
    )
    assert result.ok is False
    assert result.findings, "a failing gate must always give the writer something"


def test_the_artifact_declares_the_dependency_its_release_gate_needs(
    tmp_path, stub_coder
):
    """The delivered platform ships scripts/release_gate.py but never declared
    pytest, so a customer running the clone-and-test gate hit the same
    ModuleNotFoundError. It now ships requirements-dev.txt and the gate says
    what to install instead of dying."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    assert runner.run().ok

    dev = out / "requirements-dev.txt"
    assert dev.is_file(), "artifact has no requirements-dev.txt"
    assert "pytest" in dev.read_text(encoding="utf-8")

    gate = (out / "scripts" / "release_gate.py").read_text(encoding="utf-8")
    assert "requirements-dev.txt" in gate
    assert "CANNOT RUN" in gate
    assert "-m" in gate and "not pilot" in gate
    # `{sys.executable}` is a one-element set; py_compile cannot catch it.
    assert "[sys.executable," in gate
    assert "[{sys.executable}" not in gate


def test_runner_readme_installs_dev_deps_before_pytest(tmp_path, stub_coder):
    """README 'Run it' must not tell a stranger to pytest after only
    requirements.txt — pytest lives in requirements-dev.txt on the runner path."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    assert runner.run().ok

    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "## Run it" in readme
    run_it = readme.split("## Run it", 1)[1].split("\n## ", 1)[0]
    assert "requirements-dev.txt" in run_it
    assert "pytest" in run_it
    assert run_it.index("requirements-dev.txt") < run_it.index("pytest")


def test_release_gate_template_passes_this_interpreter_not_a_set():
    """The live winery-hospitality export crashed `scripts/release_gate.py`
    with TypeError: expected str, ... not set."""
    from app.factory.build.roles import _render_release_gate

    src = _render_release_gate("Cask & Guest Tasting Room")
    assert "[sys.executable," in src
    assert "[{sys.executable}" not in src
    compile(src, "release_gate.py", "exec")


def test_phase_wall_clock_caps_the_writer_deadline(tmp_path, stub_coder):
    """An explicit per-phase cap still binds RoleContext.deadline.

    Production Store-green uses a 90-minute phase default so WRITER can
    code; this test pins that an operator-set 30s cap still wins over the
    2-hour build wall.
    """
    import time as _time

    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.runner import BuildBudget

    seen = {}
    real = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def wrapping(ctx):
        seen["deadline"] = ctx.deadline
        seen["now"] = _time.monotonic()
        return real(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = wrapping
    started = _time.monotonic()
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(wall_clock_s=7200.0, phase_wall_clock_s=30.0),
    )
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    assert seen["deadline"] is not None
    # 30s phase cap, not the 7200s build wall.
    assert seen["deadline"] - started < 600
    assert seen["deadline"] - seen["now"] <= 30.0 + 2.0


def test_a_restarted_server_frees_the_floor_in_minutes_not_half_an_hour(tmp_path, monkeypatch):
    """Live: the factory server exited mid-WRITER (FleetOps, 09:54). The
    build read "building" for 20+ minutes, and while it does the Floor
    disables the message box -- the owner could not even type 'continue'."""
    from app.factory import build_jobs

    out = tmp_path / "product"
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="fleetops", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    old = time.time() - (build_jobs._ORPHAN_AFTER_S + 60)
    os.utime(out / "build_ledger.jsonl", (old, old))
    lines = []
    ts = (datetime.now(timezone.utc) - timedelta(seconds=build_jobs._ORPHAN_AFTER_S + 60)).isoformat(timespec="seconds")
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            payload["ts"] = ts
            lines.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    os.utime(out / "build_ledger.jsonl", (old, old))

    # The thread is alive: quiet, but still building.
    monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda product_id: True)
    assert build_status(out)["state"] == "building"

    # The thread is gone: an orphan, said at once.
    monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda product_id: False)
    status = build_status(out)
    assert status["state"] == "stalled"
    assert "restarted" in status["detail"] and "continue" in status["detail"]
