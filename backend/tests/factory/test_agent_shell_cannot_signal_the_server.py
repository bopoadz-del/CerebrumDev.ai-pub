"""A coding agent's shell can never signal the Factory server.

Live 2026-10-07 22:03:48 UTC and 2026-10-08 03:19:21 UTC (cerebrumdev-backend,
no deploy): the server logged a clean "Shutting down" while /ready answered
200 every 30 s, the ALB never saw the target unhealthy and CPU was 8-20 %.
ECS recorded EssentialContainerExited, exit 0 -- the container stopped
itself. Both times a WRITER CLI was mid-pass, and that CLI shared the
server's process group, so a group-wide signal from the agent's shell
(``kill 0``, a ``trap 'kill 0' EXIT`` around a probe server) reached
uvicorn and every in-flight build died with it.

The POSIX tests run the real call site in a throwaway "server" process
(its own session, so nothing here can signal pytest) whose agent sends
``kill -TERM 0``. On master that server dies of SIGTERM; it must survive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest import mock

import pytest

from app.factory.build import agent_process, codewhale_worker
from app.factory.build.tenant_bind import bind_tenant_store

BACKEND = Path(__file__).resolve().parents[2]
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
SURVIVED = "SERVER-SURVIVED"


def _fake_agent(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_agent.sh"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo 1.0; exit 0; fi\n' + body,
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run_server(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run ``code`` as a stand-in Factory server in its own session."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(BACKEND),
        env={**os.environ, "PYTHONPATH": str(BACKEND), "ENV": "test"},
        capture_output=True,
        text=True,
        timeout=120,
        start_new_session=True,
    )


@POSIX_ONLY
def test_the_writer_cli_cannot_signal_its_server(tmp_path):
    agent = _fake_agent(tmp_path, "kill -TERM 0\nsleep 1\n")
    result = _run_server(
        f"""
        from pathlib import Path
        from unittest import mock
        from app.factory.build import codewhale_worker
        from app.factory.build.tenant_bind import bind_tenant_store
        with mock.patch.object(codewhale_worker, "worker_cli_path", return_value={str(agent)!r}), \\
             mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \\
             mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"):
            try:
                codewhale_worker.run_worker_job(
                    "write the platform",
                    Path({str(tmp_path / "checkout")!r}),
                    tenant_store=bind_tenant_store("acct-isolation"),
                    progress=lambda line, info: None,
                    timeout_s=30,
                )
            except Exception as exc:
                print("job ended:", type(exc).__name__)
        print({SURVIVED!r})
        """,
        tmp_path,
    )
    assert result.returncode == 0 and SURVIVED in result.stdout, (
        f"the agent's group signal killed its server (rc={result.returncode}): "
        f"{result.stderr[-400:]}"
    )


@POSIX_ONLY
def test_the_coder_cli_cannot_signal_its_server(tmp_path):
    agent = _fake_agent(tmp_path, "echo starting\nkill -TERM 0\nsleep 1\n")
    result = _run_server(
        f"""
        import os
        from pathlib import Path
        os.environ["FACTORY_CODE_CLI"] = {str(agent)!r}
        from app.factory.build.authority import BuildRole
        from app.factory.build.brief_compiler import compile_brief
        from app.factory.build.coder_session import dispatch_compiled_brief
        from app.factory.build.roles_models import RoleContext
        from app.factory.build.workspace import RoleWorkspace

        class Cap:
            capability_id = "analytics_surface"
            block_ids = ("analytics",)
            strategy = "REUSE"
            notes = "agg"
        class Plan:
            capabilities = (Cap(),)
        class Blueprint:
            product_name = "Probe"
            product_id = "probe"
            vertical = "product"
            summary = "probe"
        ws = RoleWorkspace(BuildRole.WRITER, Path({str(tmp_path / "build")!r}))
        ctx = RoleContext(role=BuildRole.WRITER, workspace=ws, blueprint=Blueprint(), plan=Plan(), state={{}})
        compiled = compile_brief(ctx.blueprint, ctx.plan, store_ids={{"analytics"}})
        ws.write_text(Path("docs") / "coder_brief.md", compiled.text)
        ws.write_text(Path("docs") / "coder_session.log", "")
        try:
            dispatch_compiled_brief(ctx, compiled)
        except Exception as exc:
            print("dispatch ended:", type(exc).__name__)
        print({SURVIVED!r})
        """,
        tmp_path,
    )
    assert result.returncode == 0 and SURVIVED in result.stdout, (
        f"the agent's group signal killed its server (rc={result.returncode}): "
        f"{result.stderr[-400:]}"
    )


@POSIX_ONLY
def test_stopping_an_agent_stops_what_it_started(tmp_path):
    marker = tmp_path / "grandchild.pid"
    proc = subprocess.Popen(
        ["sh", "-c", f"sleep 60 & echo $! > {marker}; wait"],
        **agent_process.agent_popen_kwargs(),
    )
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.05)
    grandchild = int(marker.read_text().strip())
    agent_process.kill_agent_tree(proc)
    proc.wait(timeout=10)
    for _ in range(100):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        # A killed-but-unreaped child shows as a zombie; reaping is init's job.
        stat = Path(f"/proc/{grandchild}/stat")
        if stat.exists() and stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the agent's probe process outlived the agent")


def test_the_writer_cli_is_spawned_in_its_own_session(tmp_path, monkeypatch):
    # Platform-independent: whatever the host, the call site asks for it.
    monkeypatch.setattr(agent_process, "_posix", lambda: True)
    seen = {}

    class _Done:
        stdout = None
        stderr = None
        returncode = 0
        pid = None

        def __init__(self, *a, **k):
            import io
            import json

            seen.update(k)
            self.stdout = io.StringIO(json.dumps({"status": "completed"}) + "\n")
            self.stderr = io.StringIO("")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.returncode = 9

    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen", side_effect=_Done):
        codewhale_worker.run_worker_job(
            "write the platform",
            tmp_path / "checkout",
            tenant_store=bind_tenant_store("acct-isolation"),
            progress=lambda line, info: None,
            timeout_s=5,
        )
    assert seen.get("start_new_session") is True


def test_a_fake_process_without_a_group_is_still_stopped():
    stopped = []

    class _Fake:
        pid = None

        def kill(self):
            stopped.append("kill")

        def terminate(self):
            stopped.append("terminate")

    agent_process.kill_agent_tree(_Fake())
    agent_process.terminate_agent_tree(_Fake())
    assert stopped == ["kill", "terminate"]
