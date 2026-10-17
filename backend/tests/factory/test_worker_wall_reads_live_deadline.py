"""The codewhale worker's wall reads the run's LIVE deadline, never a snapshot.

Live 2026-10-06 (b5531c12, smoke session sess_1ab02a90357f403b): the WRITER
was still producing work at 29m51s (STEP 9 reported, files changing) and was
killed at "codewhale_worker_failed: worker_timed_out: headless job exceeded
1800.0s". The runner's budget ramp lifts ``deadline_box['at']`` while a CLI
model call is in flight (its pulse fires on every relayed worker NOTE), but
``run_worker_job`` fixed ``deadline = monotonic() + timeout`` once at
dispatch -- ``_writer_worker_timeout_s`` was ~1800s at phase start -- and
never read the box again. The ramp moved a deadline nobody was waiting on.
"""

from __future__ import annotations

import io
import json
import time
from unittest import mock

from app.factory.build import codewhale_worker
from app.factory.build.tenant_bind import bind_tenant_store


class _SlowProc:
    """A CLI that answers after ``seconds`` with a completed summary."""

    def __init__(self, seconds: float):
        self._done_at = time.monotonic() + seconds
        self.stdout = io.StringIO(
            json.dumps({"status": "completed", "termination_reason": "resolved"}) + "\n"
        )
        self.stderr = io.StringIO("")
        self.returncode = None

    def wait(self, timeout=None):
        import subprocess

        if self.returncode is not None:
            return self.returncode
        if time.monotonic() >= self._done_at:
            self.returncode = 0
            return 0
        time.sleep(min(timeout or 0.05, 0.05))
        raise subprocess.TimeoutExpired("codewhale", timeout)

    def kill(self):
        self.returncode = 9


def _run(tmp_path, proc, **kw):
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen", side_effect=lambda *a, **k: proc):
        return codewhale_worker.run_worker_job(
            "write the platform",
            tmp_path / "checkout",
            tenant_store=bind_tenant_store("acct-live-wall"),
            progress=lambda line, info: None,
            **kw,
        )


def test_a_ramped_deadline_keeps_the_worker_alive_past_its_dispatch_wall(tmp_path):
    # The run's live box: the phase wall at dispatch, then the ramp lifts it
    # (as _extend_wall does when the inspect pulse sees the call in flight).
    box = {"at": time.monotonic() + 0.3}

    def live_time_left():
        if time.monotonic() > box["at"] - 0.2:
            box["at"] = time.monotonic() + 5.0  # the ramp
        return box["at"] - time.monotonic()

    receipt = _run(tmp_path, _SlowProc(1.0), timeout_s=0.3, live_time_left=live_time_left)
    assert receipt.status == "completed"


def test_without_a_ramp_the_worker_still_stops_at_its_wall(tmp_path):
    box = {"at": time.monotonic() + 0.3}  # nothing lifts it: no call in flight

    try:
        _run(
            tmp_path,
            _SlowProc(5.0),
            timeout_s=0.3,
            live_time_left=lambda: box["at"] - time.monotonic(),
        )
    except codewhale_worker.WorkerError as exc:
        assert codewhale_worker.WORKER_TIMED_OUT in str(exc)
    else:
        raise AssertionError("an un-ramped worker outlived its wall")


def test_a_shrinking_live_deadline_never_cuts_the_dispatch_wall(tmp_path):
    # The live value only ever extends the wall; the dispatch floor holds.
    receipt = _run(tmp_path, _SlowProc(0.3), timeout_s=1.0, live_time_left=lambda: 0.0)
    assert receipt.status == "completed"


def test_the_writer_hands_the_worker_the_runs_live_deadline(tmp_path, monkeypatch):
    from app.factory.build import roles_handlers
    from app.factory.build.roles_models import RoleContext

    clock = {"t": 100.0}
    box = {"at": 100.0 + 2000.0, "clock": lambda: clock["t"]}
    ctx = mock.Mock(spec=RoleContext)
    ctx.deadline = None
    ctx.deadline_box = box
    ctx.coder_time_left = lambda: RoleContext.coder_time_left(ctx)

    live = roles_handlers._writer_worker_live_time_left(ctx)
    assert live is not None
    before = live()
    box["at"] = 100.0 + 7000.0  # the ramp lifted the run's deadline
    after = live()
    assert after - before == 5000.0, "the worker must see the lifted box"
