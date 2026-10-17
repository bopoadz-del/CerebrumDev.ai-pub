"""The budget ramp sees a codewhale-worker call as in flight -- the live path.

Live 2026-10-06 (ea37fb3c, smoke session sess_d44683d48aca4b5a): even with the
worker reading the run's live deadline (#665), the WRITER was killed at
"worker_timed_out: headless job exceeded 1800.0s". The runner's ramp
(_maybe_cli_phase_ramp) only lifts the box when budget_inspect._cli_flight
says a CLI call is in flight, and _cli_flight counted a call as dispatched
only when its NOTE's source equals "coder CLI". The codewhale writer's
in-flight NOTE is relayed with source "codewhale_worker" -- so on the path
production actually runs, the ramp never fired and the live reader had
nothing to read.

These tests use the real runner, the real WRITER relay and the real worker
wait loop; only the codewhale subprocess is a stand-in.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from unittest import mock

from app.factory.blueprint import load_blueprint
from app.factory.build import budget_inspect, codewhale_worker
from app.factory.build.authority import BuildRole
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build.tenant_bind import bind_tenant_store

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


class _Ledgerish:
    def __init__(self, payload):
        self.payload = payload


def test_a_codewhale_in_flight_note_counts_as_a_cli_call():
    # The exact payload the WRITER relay writes for the worker's open NOTE.
    opened = _Ledgerish({"model_call": True, "deadline_s": 1800.0,
                         "provider": "deepseek", "source": "codewhale_worker"})
    flight = budget_inspect._cli_flight([opened], {})
    assert flight["cli_in_flight"] is True


def test_a_non_agent_model_call_is_not_a_cli_call():
    # A model call from the Factory's own machinery is not the coding agent.
    other = _Ledgerish({"model_call": True, "deadline_s": 60.0, "source": "factory event_bus emit"})
    assert budget_inspect._cli_flight([other], {})["cli_in_flight"] is False


class _SlowCli:
    """A codewhale process that needs ``seconds`` to finish."""

    def __init__(self, seconds):
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


def test_the_runner_ramps_a_live_codewhale_writer_past_its_phase_wall(tmp_path, monkeypatch):
    """Phase wall 1.5 s; the CLI needs 3 s. Before the fix the worker is
    killed at its dispatch wall (the ramp never fires on the codewhale path);
    after it, the ramp lifts the box and the worker finishes."""
    from app.factory.build import roles_handlers
    from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

    # The grace the writer subtracts would swallow a test-scale wall.
    monkeypatch.setattr("app.factory.llm_watchdog.MODEL_CALL_GRACE_S", 0.0)
    monkeypatch.setattr(roles_handlers, "_writer_worker_timeout_s", lambda ctx: 1.5)
    monkeypatch.setattr(codewhale_worker, "HEARTBEAT_EVERY_S", 0.2)
    assert MODEL_CALL_GRACE_S >= 0  # imported to prove the attribute exists

    outcome = {}

    def writer_via_worker(ctx):
        ctx.state["tenant_store"] = bind_tenant_store("acct-ramp-live")
        with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
             mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
             mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
             mock.patch.object(codewhale_worker.subprocess, "Popen",
                               side_effect=lambda *a, **k: _SlowCli(3.0)):
            try:
                codewhale_worker.run_worker_job(
                    "write the platform",
                    ctx.workspace.workspace,
                    tenant_store=ctx.state["tenant_store"],
                    progress=lambda line, info: ctx.note(
                        line[:200], source="codewhale_worker",
                        **roles_handlers.relayed_call_fields(info),
                    ),
                    timeout_s=roles_handlers._writer_worker_timeout_s(ctx),
                    live_time_left=roles_handlers._writer_worker_live_time_left(ctx),
                )
                outcome["worker"] = "finished"
            except codewhale_worker.WorkerError as exc:
                outcome["worker"] = str(exc)
        return RoleResult(ok=True, detail="writer pass")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer_via_worker
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        roles=roles,
        budget=BuildBudget(
            max_rework=1,
            wall_clock_s=60.0,
            phase_wall_clock_s=1.5,
            hard_ceiling_s=60.0,
        ),
    )
    runner.run()

    assert outcome.get("worker") == "finished", (
        "the runner never ramped the codewhale writer: " + str(outcome.get("worker"))
    )
