"""The writer's PROGRESS watchdog: an idle pass is killed and retried once.

The wall (``worker_timed_out``) bounds the whole headless pass; it cannot see
a live-but-silent CLI sitting inside one model call that never returns. The
progress watchdog can: no new step, no new file on disk and no new stdout
line for ``FACTORY_CODEWHALE_IDLE_S`` seconds is a stall -- the agent tree is
killed, the pass retried ONCE with a fresh process on the same staged
workspace, and a second stall fails the phase as ``worker_stalled``, never
``worker_timed_out``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.codewhale_worker import (
    PROCESS_CAP_ENV,
    PROFILE_ENV,
    STALL_FILES,
    STALL_IDLE_S,
    STALL_STEPS,
    TENANT_CAP_ENV,
    WORKER_STALLED,
    InProcessSlotCounter,
    WorkerError,
    run_worker_job_with_stall_retry,
    set_slot_counter,
)
from app.factory.build.tenant_bind import TenantStoreBinding


def _binding(digest: str = "stall") -> TenantStoreBinding:
    return TenantStoreBinding(
        tenant_key=f"key_{digest}",
        store_dir=Path(f"/tmp/tenant-{digest}"),
        bound_at=0.0,
    )


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch):
    for name in (PROCESS_CAP_ENV, TENANT_CAP_ENV, PROFILE_ENV):
        monkeypatch.delenv(name, raising=False)
    previous = set_slot_counter(InProcessSlotCounter())
    try:
        yield
    finally:
        set_slot_counter(previous)


@pytest.mark.parametrize("idle_s", [1.0, 2.0])
def test_idle_writer_is_killed_and_retried_exactly_once(monkeypatch, tmp_path, idle_s):
    """A fake codewhale that prints two steps then sleeps forever is killed at
    the configured idle limit and retried exactly once; the second stall fails
    as worker_stalled, never worker_timed_out."""
    import subprocess as sp

    from app.factory.build import codewhale_worker as worker_mod

    # The fake CLI: --version answers the pre-flight probe; a real pass writes
    # two STEP lines to the agent's progress log, then hangs forever.
    fake = tmp_path / "fake-codewhale"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codewhale 0.9.13-test"; exit 0; fi\n'
        'printf "writer: STEP 1\\n" >> docs/writer_progress.log\n'
        'printf "writer: STEP 2\\n" >> docs/writer_progress.log\n'
        "sleep 300\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: str(fake))
    # The DNS probe is telemetry; a real one costs a resolver round-trip per
    # pass and proves nothing about the idle watchdog. Pin it to empty.
    monkeypatch.setattr(worker_mod, "_dns_probe", lambda cwd, env: {})
    monkeypatch.setenv("FACTORY_CODEWHALE_IDLE_S", str(idle_s))

    pass_spawns: list = []
    real_popen = sp.Popen

    def counting_popen(argv, *args, **kwargs):
        if any(a == "exec" for a in argv):
            pass_spawns.append(list(argv))
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(sp, "Popen", counting_popen)

    progress_lines: list = []
    with pytest.raises(WorkerError) as exc:
        run_worker_job_with_stall_retry(
            "build it",
            tmp_path,
            tenant_store=_binding("stall"),
            progress=lambda line, info: progress_lines.append((line, info)),
        )

    assert WORKER_STALLED in str(exc.value)
    assert "worker_timed_out" not in str(exc.value)
    # Retried exactly once: the original pass plus one fresh process.
    assert len(pass_spawns) == 2, pass_spawns

    stall_events = [
        info
        for _, info in progress_lines
        if isinstance(info, dict) and info.get("stall") == WORKER_STALLED
    ]
    assert len(stall_events) == 2, stall_events
    for info in stall_events:
        assert STALL_IDLE_S in info
        assert STALL_FILES in info
        assert STALL_STEPS in info
        # Killed at the configured idle limit, never earlier, never at the wall.
        assert info[STALL_IDLE_S] >= idle_s * 0.5


def test_emit_worker_stalled_skips_a_missing_callback():
    """The ledger event is telemetry: no progress callback means no call, no
    error -- the guard is what keeps a missing callback from being invoked."""
    from app.factory.build.codewhale_worker import (
        _WorkerStall,
        _emit_worker_stalled,
    )

    # Must return normally without ever attempting to call ``None``.
    _emit_worker_stalled(None, _WorkerStall(480.0, 3, 7), 1)
