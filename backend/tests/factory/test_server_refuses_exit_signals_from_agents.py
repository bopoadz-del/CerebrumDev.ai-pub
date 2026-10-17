"""While a coding agent runs, the Factory server does not obey an exit signal.

Live 2026-10-09 18:57:52 UTC (cycle 6, 75a5a1ef, cerebrumdev-backend): uvicorn
logged ``Shutting down`` in the same second a WRITER CLI exited; ECS recorded
no service-initiated stop (no "has stopped 1 running tasks"; it deregistered
the target 30 s AFTER the shutdown), memory peaked at 33 %. The container
stopped itself -- the 2026-10-07/08 signature -- with every agent already in
its own session (#700), so the signal was aimed at the server by name or pid
(``pkill -f "uvicorn app.main"`` stopping a probe server reaches the Factory
server too: every product serves ``app.main:app``). All four writers of the
cycle were orphaned mid-rework.

uvicorn runs as PID 1, so the kernel already drops SIGKILL sent from inside
the container; SIGTERM/SIGINT get through only because uvicorn installs
handlers. The guard wraps those handlers: while an agent is live (or ended
moments ago -- its last command can land after its exit is noticed), the
signal is refused and counted. An ECS stop is then finished by ECS's own
SIGKILL after stopTimeout; it was going to kill those builds anyway.
"""

from __future__ import annotations

import os
import signal

import pytest

from app.factory.build import agent_process

posix_only = pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name != "posix", reason="POSIX only")


class _Proc:
    def __init__(self, pid: int, alive: bool = True):
        self.pid = pid
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    agent_process._LIVE.clear()
    agent_process._RECENT_EXITS.clear()
    agent_process.reset_exit_signal_stats()
    saved = {sig: signal.getsignal(sig) for sig in agent_process.GUARDED_SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    agent_process._LIVE.clear()
    agent_process._RECENT_EXITS.clear()


def _recorder():
    seen = []

    def handler(sig, frame):
        seen.append(sig)

    return handler, seen


@posix_only
def test_an_exit_signal_while_an_agent_runs_is_refused():
    handler, seen = _recorder()
    signal.signal(signal.SIGTERM, handler)
    assert agent_process.guard_exit_signals() is True
    agent_process.track_agent(_Proc(4242))

    os.kill(os.getpid(), signal.SIGTERM)

    assert seen == []
    assert agent_process.exit_signal_stats()["refused"] == 1


@posix_only
def test_with_no_agent_running_the_server_still_stops():
    handler, seen = _recorder()
    signal.signal(signal.SIGTERM, handler)
    agent_process.guard_exit_signals()

    os.kill(os.getpid(), signal.SIGTERM)

    assert seen == [signal.SIGTERM]
    assert agent_process.exit_signal_stats()["refused"] == 0


@posix_only
def test_an_agent_that_just_exited_still_counts(monkeypatch):
    """Its last command can land after its exit is noticed."""
    handler, seen = _recorder()
    signal.signal(signal.SIGTERM, handler)
    agent_process.guard_exit_signals()
    proc = _Proc(4343)
    agent_process.track_agent(proc)
    proc.alive = False
    agent_process.live_agent_sessions()  # the roster notices the exit

    os.kill(os.getpid(), signal.SIGTERM)
    assert seen == []

    clock = agent_process._monotonic() + agent_process.EXIT_SIGNAL_GRACE_S + 1
    monkeypatch.setattr(agent_process, "_monotonic", lambda: clock)
    os.kill(os.getpid(), signal.SIGTERM)
    assert seen == [signal.SIGTERM]


@posix_only
def test_sigint_is_guarded_too():
    handler, seen = _recorder()
    signal.signal(signal.SIGINT, handler)
    agent_process.guard_exit_signals()
    agent_process.track_agent(_Proc(4444))
    os.kill(os.getpid(), signal.SIGINT)
    assert seen == []


@posix_only
def test_guarding_twice_does_not_stack():
    handler, _ = _recorder()
    signal.signal(signal.SIGTERM, handler)
    agent_process.guard_exit_signals()
    first = signal.getsignal(signal.SIGTERM)
    agent_process.guard_exit_signals()
    assert signal.getsignal(signal.SIGTERM) is first


@posix_only
def test_a_default_or_ignored_disposition_is_left_alone():
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    agent_process.guard_exit_signals()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert agent_process.exit_signal_stats()["guarded"] is False


def test_off_the_main_thread_it_reports_not_guarded():
    import threading

    out = []
    t = threading.Thread(target=lambda: out.append(agent_process.guard_exit_signals()))
    t.start()
    t.join()
    assert out == [False]


@posix_only
def test_the_isolation_report_says_whether_the_guard_is_installed(monkeypatch):
    monkeypatch.setattr(agent_process, "group_kill_probe", lambda: True)
    assert agent_process.process_isolation_report()["exit_signals_guarded"] is False
    handler, _ = _recorder()
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    agent_process.guard_exit_signals()
    report = agent_process.process_isolation_report()
    assert report["exit_signals_guarded"] is True
    assert report["exit_signals_refused"] == 0


def test_the_app_installs_the_guard_at_startup():
    import inspect

    from app import main

    assert "guard_exit_signals()" in inspect.getsource(main._lifespan)


_REAL_SERVER = r"""
import contextlib, subprocess, sys
import uvicorn
from fastapi import FastAPI
from app.factory.build import agent_process

@contextlib.asynccontextmanager
async def lifespan(app):
    agent_process.guard_exit_signals()
    if sys.argv[1] == "agent":
        agent_process.track_agent(subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"], **agent_process.agent_popen_kwargs()))
    print("ready", flush=True)
    yield

uvicorn.Server(uvicorn.Config(FastAPI(lifespan=lifespan), host="127.0.0.1", port=0, log_level="warning")).run()
"""


@posix_only
@pytest.mark.parametrize("agent", ["agent", "none"])
def test_a_real_uvicorn_server_refuses_sigterm_only_while_an_agent_runs(agent, tmp_path):
    """uvicorn installs its handlers before lifespan startup, so the guard
    wraps the real ones -- proven on the installed uvicorn, not assumed."""
    import subprocess
    import sys
    import time

    script = tmp_path / "server.py"
    script.write_text(_REAL_SERVER)
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    proc = subprocess.Popen(
        [sys.executable, str(script), agent], cwd=backend, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": backend}, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if agent == "agent":
            assert proc.poll() is None, proc.stderr.read()
        else:
            assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        time.sleep(0)
