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


@pytest.mark.parametrize(
    ("rel_parts", "expected"),
    [
        # The writer CLI's own runtime home is state, never output.
        ((".codewhale-home", "tmp", "churn"), 0),
        # A product source file IS output.
        (("app", "main.py"), 1),
    ],
)
def test_authored_file_count_treats_state_and_product_differently(
    tmp_path, rel_parts, expected
):
    """Only product files count as authored output; the CLI's own home is
    excluded from the idle watchdog's file-write signal."""
    from app.factory.build.codewhale_worker import count_authored_files

    target = tmp_path.joinpath(*rel_parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    assert count_authored_files(tmp_path) == expected


def test_a_heartbeating_worker_with_no_output_is_stalled(monkeypatch, tmp_path):
    """Two-input rule, input 1: the factory heartbeat narrates silence without
    resetting the idle clock. A worker that produces no step, file or stdout
    line is declared stalled at the idle limit even while heartbeats fire."""
    from app.factory.build import codewhale_worker as worker_mod

    fake = tmp_path / "fake-codewhale"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codewhale 0.9.13-test"; exit 0; fi\n'
        "sleep 300\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: str(fake))
    monkeypatch.setattr(worker_mod, "_dns_probe", lambda cwd, env: {})
    # Fire the heartbeat many times inside the idle window: narration must
    # never look like work.
    monkeypatch.setattr(worker_mod, "HEARTBEAT_EVERY_S", 0.1)
    monkeypatch.setenv("FACTORY_CODEWHALE_IDLE_S", "1.0")

    progress_lines: list = []
    with pytest.raises(WorkerError) as exc:
        run_worker_job_with_stall_retry(
            "build it",
            tmp_path,
            tenant_store=_binding("heartbeat"),
            progress=lambda line, info: progress_lines.append((line, info)),
        )

    assert WORKER_STALLED in str(exc.value)
    assert "worker_timed_out" not in str(exc.value)
    assert any(
        line.startswith("writer working") for line, _ in progress_lines
    ), "heartbeats fired yet the writer was still declared stalled"


def test_a_worker_writing_product_files_is_not_stalled(monkeypatch, tmp_path):
    """Two-input rule, input 2: files landing on disk ARE progress. A writer
    that keeps creating product files runs to the wall, never a stall."""
    from app.factory.build import codewhale_worker as worker_mod

    fake = tmp_path / "fake-codewhale"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codewhale 0.9.13-test"; exit 0; fi\n'
        "mkdir -p app\n"
        "i=0\n"
        "while :; do\n"
        "  i=$((i+1))\n"
        '  echo "x" > "app/gen_${i}.py"\n'
        "  sleep 0.2\n"
        "done\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: str(fake))
    monkeypatch.setattr(worker_mod, "_dns_probe", lambda cwd, env: {})
    monkeypatch.setenv("FACTORY_CODEWHALE_IDLE_S", "0.5")

    with pytest.raises(WorkerError) as exc:
        run_worker_job_with_stall_retry(
            "build it",
            tmp_path,
            tenant_store=_binding("writer"),
            timeout_s=2.0,
        )

    assert WORKER_STALLED not in str(exc.value)
    assert "worker_timed_out" in str(exc.value)


def test_a_worker_churning_its_own_home_is_stalled(monkeypatch, tmp_path):
    """The live shape the fix closes: the CLI churns its own runtime state
    while the model call hangs. State files are not authored output, so the
    idle clock still elapses and the pass is declared stalled at the idle
    limit -- not left to die at the wall."""
    from app.factory.build import codewhale_worker as worker_mod

    fake = tmp_path / "fake-codewhale"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codewhale 0.9.13-test"; exit 0; fi\n'
        "mkdir -p .codewhale-home/tmp\n"
        "i=0\n"
        "while :; do\n"
        "  i=$((i+1))\n"
        '  echo "x" > ".codewhale-home/tmp/churn_${i}"\n'
        "  sleep 0.2\n"
        "done\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: str(fake))
    monkeypatch.setattr(worker_mod, "_dns_probe", lambda cwd, env: {})
    monkeypatch.setenv("FACTORY_CODEWHALE_IDLE_S", "1.0")

    with pytest.raises(WorkerError) as exc:
        run_worker_job_with_stall_retry(
            "build it",
            tmp_path,
            tenant_store=_binding("churn"),
        )

    assert WORKER_STALLED in str(exc.value)
    assert "worker_timed_out" not in str(exc.value)
