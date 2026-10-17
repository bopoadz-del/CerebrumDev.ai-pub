"""A coding agent's shell never shares the Factory server's process group.

Live 2026-10-07 22:03:48 UTC and 2026-10-08 03:19:21 UTC (cerebrumdev-backend,
no deploy, no OOM): the server logged a clean ``Shutting down`` while /ready
was answering 200 every 30 s, the ALB never counted the target unhealthy
(UnHealthyHostCount 0 throughout) and CPU was 8-20 % at the moment. ECS
recorded ``EssentialContainerExited`` (exit 0) -- the container stopped
itself; ECS did not stop it. Both times a WRITER CLI was mid-pass. The CLI
is a child of the uvicorn process (PID 1) and inherited its process group,
so any group-wide signal the agent's shell sends -- ``kill 0``, a
``trap 'kill 0' EXIT`` cleanup around a probe server, ``kill -- -$PGID`` --
reaches the Factory server too, and every in-flight build dies with it.
(The writer prompt's v4 PROCESSES rule recorded the same signature on
FleetOps; a prompt is advice, not isolation.)

Every coding-agent subprocess therefore starts in its own session, and the
Factory stops an agent by signalling that session's whole group, so the
probe servers it started die with it instead of outliving the pass.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

#: Popen keyword that puts the child in a new session (POSIX setsid): its own
#: process group, no controlling terminal shared with the server.
_NEW_SESSION = "start_new_session"


def _posix() -> bool:
    return os.name == "posix"


def agent_popen_kwargs() -> Dict[str, Any]:
    """Popen keywords for any subprocess that runs a coding agent's shell."""
    if _posix():
        return {_NEW_SESSION: True}
    return {}


def _signal_group(proc: Any, sig: int) -> bool:
    """Signal the agent's whole process group; False when that is not
    possible (no real pid, not POSIX, group already gone)."""
    pid = getattr(proc, "pid", None)
    killpg = getattr(os, "killpg", None)
    if not _posix() or not isinstance(pid, int) or pid <= 0 or killpg is None:
        return False
    # The agent leads its own session, so its group id is its pid. Never
    # signal a group that is not the agent's own (it would be ours).
    try:
        if os.getpgid(pid) != pid:
            return False
    except ProcessLookupError:
        # The leader already exited; its group outlives it while anything
        # it started is still running, and still carries the leader's pid.
        pass
    except OSError:
        return False
    try:
        killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def kill_agent_tree(proc: Any) -> None:
    """Hard-stop the agent and everything it started."""
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if not _signal_group(proc, sig):
        proc.kill()


def terminate_agent_tree(proc: Any) -> None:
    """Ask the agent and everything it started to stop."""
    if not _signal_group(proc, signal.SIGTERM):
        proc.terminate()


# --- the live roster, for the post-deploy isolation check -------------------
#
# The smoke cannot see the container's process table, so the Factory answers
# for it: which session the server runs in, which sessions the live coding
# agents run in. A regression that puts an agent back in the server's session
# then fails the smoke instead of killing a build (the 2026-10-07/08 restarts).

_LIVE_LOCK = threading.Lock()
_LIVE: Dict[int, Any] = {}
#: id(proc) -> monotonic time its exit was noticed (exit-signal guard grace).
_RECENT_EXITS: Dict[int, float] = {}


def _monotonic() -> float:
    return time.monotonic()


def track_agent(proc: Any) -> Any:
    """Record a just-started coding-agent process; returns it unchanged.

    Exited processes drop out on their own (pruned on every read), so no
    call site needs a matching untrack on each of its exit paths.
    """
    with _LIVE_LOCK:
        _prune_locked()
        _LIVE[id(proc)] = proc
    return proc


def _prune_locked() -> None:
    now = _monotonic()
    for key, proc in list(_LIVE.items()):
        try:
            done = proc.poll() is not None
        except Exception:  # noqa: BLE001 -- an unreadable handle is not live
            done = True
        if done:
            _LIVE.pop(key, None)
            _RECENT_EXITS[key] = now
    for key, ended in list(_RECENT_EXITS.items()):
        if now - ended > EXIT_SIGNAL_GRACE_S:
            _RECENT_EXITS.pop(key, None)


def _session_of(pid: Any) -> Optional[int]:
    getsid = getattr(os, "getsid", None)
    if getsid is None or not isinstance(pid, int) or pid < 0:
        return None
    try:
        return int(getsid(pid))
    except OSError:
        return None


def live_agent_sessions() -> List[int]:
    """Session ids of the coding agents running now (POSIX; [] elsewhere)."""
    with _LIVE_LOCK:
        _prune_locked()
        procs = list(_LIVE.values())
    sessions = []
    for proc in procs:
        sid = _session_of(getattr(proc, "pid", None))
        if sid is not None:
            sessions.append(sid)
    return sessions


# --- exit signals while an agent runs ----------------------------------------
#
# Live 2026-10-09 18:57:52 UTC (cycle 6): with every agent already in its own
# session, the server still logged a clean ``Shutting down`` in the second a
# WRITER CLI exited, and ECS stopped nothing (it deregistered the target 30 s
# later). A signal aimed at the server by name or pid -- ``pkill -f "uvicorn
# app.main"`` stopping a probe server matches the Factory server too -- does
# not need a shared group. uvicorn runs as PID 1, so the kernel already drops
# SIGKILL sent from inside the container; SIGTERM/SIGINT reach it only through
# the handlers uvicorn installs. Those handlers are wrapped: while an agent is
# live, or ended within EXIT_SIGNAL_GRACE_S (its last command can land after
# its exit is noticed), the signal is refused and counted. An ECS stop is then
# completed by ECS's SIGKILL after stopTimeout -- those builds were dying
# either way -- and with no agent running the server stops exactly as before.

GUARDED_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGINT") if hasattr(signal, n))
EXIT_SIGNAL_GRACE_S = 30.0
_EXIT_STATS: Dict[str, Any] = {"refused": 0, "last_refused_signal": None}


def reset_exit_signal_stats() -> None:
    _EXIT_STATS.update(refused=0, last_refused_signal=None)


def _agents_hot() -> bool:
    with _LIVE_LOCK:
        _prune_locked()
        return bool(_LIVE) or bool(_RECENT_EXITS)


class _ExitSignalGuard:
    """A signal handler that defers to the wrapped one unless an agent is hot."""

    def __init__(self, inner: Callable[..., Any]):
        self.inner = inner

    def __call__(self, sig: int, frame: Any) -> Any:
        if _agents_hot():
            _EXIT_STATS["refused"] = int(_EXIT_STATS["refused"]) + 1
            _EXIT_STATS["last_refused_signal"] = int(sig)
            try:
                os.write(2, (
                    f"exit-signal guard: refused signal {int(sig)} while a coding agent runs "
                    f"(refused so far: {_EXIT_STATS['refused']})\n"
                ).encode())
            except OSError:
                pass
            return None
        return self.inner(sig, frame)


def guard_exit_signals() -> bool:
    """Wrap the installed SIGTERM/SIGINT handlers (main thread only). True when
    every guarded signal with a Python handler now carries the guard."""
    if threading.current_thread() is not threading.main_thread():
        return False
    guarded = False
    for sig in GUARDED_SIGNALS:
        current = signal.getsignal(sig)
        if isinstance(current, _ExitSignalGuard):
            guarded = True
            continue
        if not callable(current) or current in (signal.SIG_DFL, signal.SIG_IGN):
            continue
        signal.signal(sig, _ExitSignalGuard(current))
        guarded = True
    return guarded


def exit_signal_stats() -> Dict[str, Any]:
    """Is the guard installed on every handled exit signal; how often it refused."""
    handlers = [signal.getsignal(sig) for sig in GUARDED_SIGNALS]
    guarded = bool(handlers) and all(isinstance(h, _ExitSignalGuard) for h in handlers)
    return {"guarded": guarded, **_EXIT_STATS}


# --- the group-kill probe -----------------------------------------------------
#
# The roster above proves which session live agents sit in; it cannot prove
# what a group-wide kill from an agent's shell actually reaches. The probe
# does: it starts a child exactly as an agent is started (agent_popen_kwargs)
# and the child sends SIGTERM to its whole process group -- ``kill 0``, the
# 2026-10-07/08 signature. Contained means the child died of its own signal
# and the server is still here to say so. A child that finds itself in the
# server's group refuses to signal (exit 97): a regression reads as "not
# contained" on the smoke instead of stopping the live server.

_PROBE_REFUSED = 97
_PROBE_SURVIVED = 98
_PROBE_SOURCE = (
    "import os, signal, sys, time\n"
    "if os.getpgid(0) == int(sys.argv[1]):\n"
    f"    sys.exit({_PROBE_REFUSED})\n"
    "os.kill(0, signal.SIGTERM)\n"
    "time.sleep(5)\n"
    f"sys.exit({_PROBE_SURVIVED})\n"
)


def group_kill_probe(timeout_s: float = 15.0) -> Optional[bool]:
    """Does ``kill -TERM 0`` from an agent-spawned child stay in its own group?

    None when it cannot be judged (not POSIX, the child could not start or
    did not finish in time); True only when the child died of its own SIGTERM.
    """
    if not _posix() or not hasattr(os, "getpgid"):
        return None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _PROBE_SOURCE, str(os.getpgid(0))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **agent_popen_kwargs(),
        )
    except OSError:
        return None
    try:
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_agent_tree(proc)
        proc.wait()
        return None
    return code == -signal.SIGTERM


def process_isolation_report() -> Dict[str, Any]:
    """Is every live coding agent outside the server's session?

    ``isolated`` needs both halves: no live agent shares the server's session
    now, and (POSIX) the spawn path still asks for a new session -- so the
    check proves something even when no agent happens to be running -- and
    (POSIX) a real ``kill 0`` from an agent-spawned child stayed in that
    child's group while this server kept answering. Counts and booleans
    only: no pid or session id leaves the process.
    """
    posix = _posix()
    server_sid = _session_of(0)
    agents = live_agent_sessions()
    spawn_isolated = agent_popen_kwargs() == ({_NEW_SESSION: True} if posix else {})
    shared = sum(1 for s in agents if server_sid is not None and s == server_sid)
    contained = group_kill_probe() if posix else None
    isolated = (
        spawn_isolated
        and shared == 0
        and (server_sid is not None or not posix)
        and (contained is True or not posix)
    )
    stats = exit_signal_stats()
    return {
        "posix": posix,
        "spawn_isolated": spawn_isolated,
        "live_agents": len(agents),
        "agents_in_server_session": shared,
        "group_kill_contained": contained,
        "isolated": isolated,
        # Whether an exit signal sent while an agent runs is refused (the
        # 2026-10-09 restart), and how many it refused since boot.
        "exit_signals_guarded": stats["guarded"],
        "exit_signals_refused": int(stats["refused"]),
    }
