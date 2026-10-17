"""The Floor shows what the writer is doing, for the whole pass.

Owner, seven minutes into a live build: "i dont see any updates for the
writer". Two separate causes, both measured on live runs:

1. The agent's first stretch -- reading the brief and the cloned blocks --
   writes no STEP line and no file for ~10 minutes (STEP 1 landed at 9m16s
   on one build and 10m01s on the next). The factory said nothing either.
2. When the agent does write its progress log it writes a burst, and the
   relay's 3s throttle DISCARDED everything after the first line: one live
   log read "STEP 1" and then "STEP 21".
"""

from __future__ import annotations

import io
import json
import time
from unittest import mock

from app.factory.build import codewhale_worker
from app.factory.build.codewhale_worker import (
    HEARTBEAT_PREFIX,
    count_authored_files,
    heartbeat_line,
    is_narration_line,
)
from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
from app.factory.build.tenant_bind import bind_tenant_store
from tests.factory.test_codewhale_writer_dispatch import (
    _ctx,
    _plant_authored_handler,
    _receipt,
)


def _run_role(tmp_path, monkeypatch, lines):
    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")
    notes: list[str] = []
    ctx.progress = lambda detail, payload: notes.append(detail)

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None, live_time_left=None, slot_wait_left=None, on_slot_wait=None):
        for line in lines:  # a burst: no time passes between lines
            progress(line, {})
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    assert _run_writer_via_codewhale_worker(ctx).ok is True
    return notes


def test_a_burst_of_step_lines_is_shown_in_full(tmp_path, monkeypatch):
    burst = [f"writer: STEP {i}: did thing {i}" for i in range(1, 21)]

    notes = _run_role(tmp_path, monkeypatch, burst)

    shown = [n for n in notes if n.startswith("writer: STEP")]
    assert shown == burst, "steps that happened were discarded by the throttle"


def test_chatty_cli_lines_are_still_throttled(tmp_path, monkeypatch):
    """The throttle keeps its job: CLI log noise does not flood the ledger."""
    noise = [f"engine turn completion settled status=ok n={i}" for i in range(50)]

    notes = _run_role(tmp_path, monkeypatch, noise)

    assert len([n for n in notes if n.startswith("engine turn")]) == 1


def test_noise_cannot_starve_a_step_line(tmp_path, monkeypatch):
    lines = ["engine turn a", "writer: STEP 1: inventory read", "engine turn b"]

    notes = _run_role(tmp_path, monkeypatch, lines)

    assert "writer: STEP 1: inventory read" in notes


def test_the_heartbeat_only_states_what_the_factory_observed():
    quiet = heartbeat_line(245, files=0, steps_seen=0)
    assert quiet.startswith(HEARTBEAT_PREFIX)
    assert "4m05s" in quiet and "No files yet" in quiet
    working = heartbeat_line(725, files=37, steps_seen=12)
    assert "12m05s" in working and "37 file(s)" in working and "12 step(s)" in working
    # It never names a step the agent did not report.
    assert "STEP" not in quiet and "STEP" not in working
    assert is_narration_line(quiet) and is_narration_line("writer: STEP 2: x")
    assert not is_narration_line("engine turn completion settled")


def test_authored_files_exclude_vendor_and_the_progress_logs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "vendor" / "blocks" / "b").mkdir(parents=True)
    (tmp_path / "vendor" / "blocks" / "b" / "block.py").write_text("x", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "writer_progress.log").write_text("STEP 1", encoding="utf-8")
    (tmp_path / "docs" / "writer_progress.jsonl").write_text("{}", encoding="utf-8")

    assert count_authored_files(tmp_path) == 1


def test_a_silent_cli_gets_a_heartbeat_and_a_talking_one_does_not(tmp_path, monkeypatch):
    """End to end through run_worker_job with a CLI that stays silent."""
    monkeypatch.setattr(codewhale_worker, "HEARTBEAT_EVERY_S", 0.6)

    class _SilentProc:
        def __init__(self):
            self.stdout = io.StringIO(
                json.dumps({"status": "completed", "termination_reason": "resolved"}) + "\n"
            )
            self.stderr = io.StringIO("")
            self.returncode = None
            self._until = time.monotonic() + 2.2

        def wait(self, timeout=None):
            import subprocess

            if time.monotonic() < self._until:
                time.sleep(min(timeout or 0.1, 0.1))
                raise subprocess.TimeoutExpired("codewhale", timeout)
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = 9

    relayed: list[str] = []
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen", side_effect=lambda *a, **k: _SilentProc()):
        receipt = codewhale_worker.run_worker_job(
            "write the platform",
            tmp_path / "checkout",
            tenant_store=bind_tenant_store("acct-heartbeat"),
            progress=lambda line, info: relayed.append(line),
        )

    assert receipt.status == "completed"
    beats = [ln for ln in relayed if ln.startswith(HEARTBEAT_PREFIX)]
    assert len(beats) >= 2, relayed
    assert "No files yet" in beats[0]


# -- the model-call NOTE is CLOSED when the CLI session ends -------------------
#
# Live, 2026-09-28: the Floor declared "coder LLM timed out after 2595s
# (deadline 1800s)" on a build whose writer session had already finished and
# whose run was still working. The codewhale path OPENED the model-call NOTE
# ("codewhale writer CLI started") and never emitted the
# "FACTORY_CODE_CLI session finished" close that build_status waits for --
# only the C-BRIEF dispatch layer (coder_session) ever emitted it. So on
# EVERY codewhale build, 1800s of wall clock after the CLI started --
# regardless of what the run was doing by then -- _model_call_overdue flipped
# the status to failed and the Floor showed CODING AGENT STOPPED over a live
# run.


class _FastProc:
    """A CLI that answers immediately with a completed JSON summary."""

    def __init__(self):
        self.stdout = io.StringIO(
            json.dumps({"status": "completed", "termination_reason": "resolved"}) + "\n"
        )
        self.stderr = io.StringIO("")
        self.returncode = None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = 9


class _HungProc:
    """A CLI that never returns: only the worker wall ends it."""

    def __init__(self):
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.returncode = None

    def wait(self, timeout=None):
        import subprocess

        if self.returncode is not None:
            return self.returncode
        time.sleep(min(timeout or 0.1, 0.1))
        raise subprocess.TimeoutExpired("codewhale", timeout)

    def kill(self):
        self.returncode = 9


def _run_real_worker(tmp_path, proc, *, timeout_s=None):
    relayed: list[tuple[str, dict]] = []
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen", side_effect=lambda *a, **k: proc):
        try:
            codewhale_worker.run_worker_job(
                "write the platform",
                tmp_path / "checkout",
                tenant_store=bind_tenant_store("acct-close-note"),
                timeout_s=timeout_s,
                progress=lambda line, info: relayed.append((line, dict(info or {}))),
            )
        except codewhale_worker.WorkerError:
            pass
    return relayed


def test_the_model_call_note_is_closed_when_the_cli_finishes(tmp_path):
    relayed = _run_real_worker(tmp_path, _FastProc())

    opened = [i for i, (d, _) in enumerate(relayed) if "CLI started" in d]
    closed = [
        (i, p) for i, (d, p) in enumerate(relayed)
        if "FACTORY_CODE_CLI session finished" in d
    ]
    assert opened, relayed
    assert closed, "the CLI finished and no close NOTE was emitted -- the " \
        "Floor will declare this build timed out 1800s after the CLI STARTED"
    assert closed[-1][0] > opened[-1], "close must land after the open"
    # model_call=False rides the payload so the relay never throttles the
    # close behind a noise line (the same guarantee the open NOTE has).
    assert closed[-1][1].get("model_call") is False


def test_the_model_call_note_is_closed_when_the_wall_kills_the_cli(tmp_path):
    relayed = _run_real_worker(tmp_path, _HungProc(), timeout_s=0.4)

    assert any(
        "FACTORY_CODE_CLI session finished" in d for d, _ in relayed
    ), "a wall-killed session left the model-call NOTE open forever"


def test_the_close_detail_is_the_one_build_status_waits_for():
    """Tie the two modules: the worker's close NOTE must carry the typed
    close state _open_model_call_note decides on (model_call.py), or the fix
    silently rots. The detail text is display only; a close without the
    typed state is not a close."""
    from types import SimpleNamespace

    from app.factory.build.model_call import CLOSED, MODEL_CALL_STATE
    from app.factory.build_jobs import _open_model_call_note

    opened = SimpleNamespace(
        detail="codewhale writer CLI started -- model call in flight",
        payload={"model_call": True, "deadline_s": 1800.0},
        ts="2026-09-28T00:00:00+00:00",
    )
    closed = SimpleNamespace(
        detail=codewhale_worker.MODEL_CALL_CLOSED_DETAIL,
        payload={"model_call": False, MODEL_CALL_STATE: CLOSED},
        ts="2026-09-28T00:20:00+00:00",
    )
    text_only = SimpleNamespace(
        detail=codewhale_worker.MODEL_CALL_CLOSED_DETAIL,
        payload={"model_call": False},
        ts="2026-09-28T00:20:00+00:00",
    )
    assert _open_model_call_note([opened]) is not None
    assert _open_model_call_note([opened, closed]) is None
    # The sentence alone decides nothing any more.
    assert _open_model_call_note([opened, text_only]) is not None
    # And the worker really emits that state on close.
    import inspect

    assert "MODEL_CALL_STATE: CLOSED" in inspect.getsource(codewhale_worker)


# -- surviving a server restart mid-WRITER -------------------------------------


def test_the_model_call_payload_reaches_the_ledger(tmp_path, monkeypatch):
    """Boot recovery and the Floor look ONLY at this payload. The relay used
    to keep the text and drop it, so no CodeWhale build was ever auto-resumed
    after a restart (live: FleetOps sat dead 20+ minutes)."""
    ctx = _ctx(tmp_path)
    _plant_authored_handler(tmp_path / "build")
    seen: list[tuple[str, dict]] = []
    ctx.progress = lambda detail, payload: seen.append((detail, dict(payload)))

    def fake_run(prompt, dest, tenant_store=None, session_id="", product_id="", progress=None, timeout_s=None, live_time_left=None, slot_wait_left=None, on_slot_wait=None):
        progress("engine turn noise", {})
        progress(
            "codewhale writer CLI started -- model call in flight",
            {"model_call": True, "deadline_s": 2400, "provider": "deepseek"},
        )
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    assert _run_writer_via_codewhale_worker(ctx).ok is True

    started = [p for d, p in seen if "CLI started" in d]
    assert started, "the CLI-start note was throttled away behind a noise line"
    assert started[0]["model_call"] is True
    assert started[0]["deadline_s"] == 2400


def test_boot_recovery_can_see_a_codewhale_build(tmp_path):
    """End of the chain: a ledger written with that payload IS an orphan."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build.orphan_recovery import is_orphaned_inflight_workspace

    out = tmp_path / "product"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="fleetops", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    assert is_orphaned_inflight_workspace(out) is False, "no payload, invisible (the old state)"

    ledger.append(
        EventKind.NOTE, role=BuildRole.WRITER,
        detail="codewhale writer CLI started -- model call in flight",
        payload={"model_call": True, "deadline_s": 2400},
    )
    assert is_orphaned_inflight_workspace(out) is True


def _runner(tmp_path):
    from pathlib import Path

    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    smoke = Path(__file__).resolve().parents[3] / "blueprints/examples/runner_smoke.yaml"
    return RoleRunner(load_blueprint(smoke), tmp_path / "build")


def test_an_interrupted_codewhale_pass_is_resumable_only_with_its_log(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    staging = tmp_path / ".build.staging-writer"
    (staging / "docs").mkdir(parents=True)

    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")
    assert runner._writer_can_resume(staging) is False, "no progress log: nothing to resume from"

    (staging / "docs" / "writer_progress.log").write_text("STEP 1: inventory\n", encoding="utf-8")
    assert runner._writer_can_resume(staging) is True

    # The in-process coder rewrites app/ wholesale: its leftovers are still wiped.
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "0")
    assert runner._writer_can_resume(staging) is False


def test_the_resumed_agent_is_told_to_continue_not_restart():
    from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt

    class _Bp:
        product_id = product_name = vertical = summary = "probe"

    fresh = render_writer_prompt(_Bp(), brief="x")
    resumed = render_writer_prompt(_Bp(), brief="x", resume=True)

    assert "RESUME" not in fresh
    assert resumed.startswith(f"<!-- {PROMPT_VERSION} -->")
    for phrase in ("Do NOT start over", "docs/writer_progress.log", "keep numbering STEP"):
        assert phrase in resumed
    assert resumed.endswith(fresh.split("-->\n", 1)[1]), "the original brief must follow unchanged"
