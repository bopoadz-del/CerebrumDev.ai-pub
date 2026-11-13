"""F-S2a: the writer's pytest tool policy -- a refusal mechanism, not a prompt line.

Every rule is pinned by two inputs that must disagree: the targeted file vs
the whole tests/ tree, the writer's env vs no policy env, zero logged
refusals vs two, a readable denial log vs a missing one.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.factory.build import codewhale_worker as worker_mod
from app.factory.build.codewhale_worker import rounds_summary_line, suite_denied_count
from app.factory.build.pytest_policy import (
    DENIALS_FILE,
    PLUGIN_MODULE,
    POLICY_DIR_NAME,
    ROOT_ENV,
    read_denials,
    stamp_policy,
)
from app.factory.build.writer_behaviour import SELF_CHECK_REL, render_self_check


def _workspace(root: Path) -> None:
    """One changed source with a test that names it, and one unrelated test."""
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_widget.py").write_text(
        "def test_widget():\n    assert 'widget'\n", encoding="utf-8"
    )
    (root / "tests" / "test_other.py").write_text(
        "def test_other():\n    assert True\n", encoding="utf-8"
    )
    stamp = root / SELF_CHECK_REL
    stamp.parent.mkdir(parents=True)
    stamp.write_text(render_self_check(), encoding="utf-8")
    past = time.time() - 600
    os.utime(stamp, (past, past))
    for rel in ("app/__init__.py", "tests/test_widget.py", "tests/test_other.py"):
        os.utime(root / rel, (past - 60, past - 60))
    # The pass's edit: newer than the stamp.
    (root / "app" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")


def _run_pytest(root: Path, env_extra: dict, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", ROOT_ENV)}
    env.update(env_extra)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (env_extra.get("PYTHONPATH"), str(root)) if p
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "selection,refused",
    [(["tests/test_widget.py"], False), (["tests"], True), ([], True)],
)
def test_writer_pytest_outside_targeted_set_is_refused_and_logged(
    tmp_path, selection, refused
):
    _workspace(tmp_path)
    home = tmp_path / ".codewhale-home"
    env = stamp_policy(home, tmp_path)
    proc = _run_pytest(tmp_path, env, *selection)
    denials = read_denials(home)
    if refused:
        assert proc.returncode == 4, proc.stdout + proc.stderr
        assert "factory policy" in proc.stderr
        assert "tests/test_widget.py" in proc.stderr
        assert len(denials) == 1
        assert denials[0]["targeted"] == ["tests/test_widget.py"]
    else:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert denials == []


@pytest.mark.parametrize("with_root,refused", [(True, True), (False, False)])
def test_policy_binds_only_where_the_writer_env_names_a_root(
    tmp_path, with_root, refused
):
    """The Factory's own suite runs carry no policy root and are never refused."""
    _workspace(tmp_path)
    home = tmp_path / ".codewhale-home"
    env = stamp_policy(home, tmp_path)
    if not with_root:
        env.pop(ROOT_ENV)
    proc = _run_pytest(tmp_path, env, "tests")
    assert (proc.returncode == 4) is refused, proc.stdout + proc.stderr
    assert len(read_denials(home)) == (1 if refused else 0)


def test_stamp_wires_the_plugin_and_resets_this_pass_log(tmp_path):
    home = tmp_path / "home"
    env = stamp_policy(home, tmp_path)
    policy = home / POLICY_DIR_NAME
    assert env["PYTEST_ADDOPTS"] == f"-p {PLUGIN_MODULE}"
    assert env["PYTHONPATH"] == str(policy)
    assert env[ROOT_ENV] == str(tmp_path)
    assert (policy / f"{PLUGIN_MODULE}.py").is_file()
    (policy / DENIALS_FILE).write_text('{"selection": []}\n', encoding="utf-8")
    assert len(read_denials(home)) == 1
    stamp_policy(home, tmp_path)
    assert read_denials(home) == []


@pytest.mark.parametrize(
    "text,count",
    [(None, None), ("", 0), ('{"a": 1}\n\nnot json\n', 2)],
)
def test_denial_log_unreadable_is_not_zero(tmp_path, text, count):
    if text is not None:
        (tmp_path / POLICY_DIR_NAME).mkdir()
        (tmp_path / POLICY_DIR_NAME / DENIALS_FILE).write_text(text, encoding="utf-8")
    assert suite_denied_count(read_denials(tmp_path)) == count
    shown = "-" if count is None else str(count)
    assert f"suite_denied={shown}" in rounds_summary_line({"suite_denied": count})


@pytest.mark.parametrize("refusals", [0, 2])
def test_run_worker_job_reports_this_pass_refusals(monkeypatch, tmp_path, refusals):
    """End to end: the writer child carries the policy env, and the refusals
    it logged come back on the receipt."""
    from app.factory.build.tenant_bind import TenantStoreBinding

    seen_env: dict = {}
    meta = {
        "type": "metadata",
        "schema": worker_mod.EXEC_STREAM_SCHEMA,
        "schema_version": 1,
        "meta": {
            "receipt_kind": "terminal",
            "provider": "prov",
            "model": "mdl",
            "status": "completed",
            "termination_reason": "resolved",
            "error_category": None,
            "error": None,
        },
    }
    tool = {
        "type": "tool_use",
        "schema": worker_mod.EXEC_STREAM_SCHEMA,
        "schema_version": 1,
        "name": "bash",
        "input": {"command": "ls"},
    }
    stdout = json.dumps(tool) + "\n" + json.dumps(meta) + "\n"

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            env = kwargs.get("env") or {}
            seen_env.update(env)
            log = Path(env["PYTHONPATH"]) / DENIALS_FILE
            with log.open("a", encoding="utf-8") as fh:
                for _ in range(refusals):
                    fh.write(json.dumps({"selection": []}) + "\n")
            self.stdout = io.StringIO(stdout)
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = 9

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: "/nonexistent/codewhale")
    # The in-confinement DNS probe spawns its own child through the same
    # (patched) subprocess layer on hosts where confinement is available;
    # stub it so only the writer spawn is observed, on every platform.
    monkeypatch.setattr(worker_mod, "_dns_probe", lambda *a, **k: {})
    binding = TenantStoreBinding(
        tenant_key="key_fs2", store_dir=Path("/tmp/tenant-fs2"), bound_at=0.0
    )
    receipt = worker_mod.run_worker_job("build it", tmp_path, tenant_store=binding)
    assert seen_env["PYTEST_ADDOPTS"] == f"-p {PLUGIN_MODULE}"
    assert seen_env[ROOT_ENV] == str(tmp_path)
    assert receipt.rounds["suite_denied"] == refusals
    assert f"suite_denied={refusals}" in rounds_summary_line(receipt.rounds)


@pytest.mark.parametrize(
    "line,entry",
    [('{"selection": []}', {"selection": []}), ("[1]", {"unparsed": "[1]"})],
)
def test_denial_entries_are_always_objects(tmp_path, line, entry):
    (tmp_path / POLICY_DIR_NAME).mkdir()
    (tmp_path / POLICY_DIR_NAME / DENIALS_FILE).write_text(
        line + "\n", encoding="utf-8"
    )
    assert read_denials(tmp_path) == [entry]


@pytest.mark.parametrize("writable", [True, False])
def test_unwritable_policy_fails_the_dispatch_named(monkeypatch, tmp_path, writable):
    """A writer without the policy is not the writer ordered: an unwritable
    policy dir is a named dispatch failure, never a silent policy-free pass."""
    from app.factory.build import pytest_policy
    from app.factory.build.tenant_bind import TenantStoreBinding

    started: list = []

    def _stamp(home, root):
        if not writable:
            raise OSError("read-only")
        return {}

    class _Started(Exception):
        pass

    def _popen(argv, **kwargs):
        started.append(argv)
        raise _Started()

    monkeypatch.setattr(pytest_policy, "stamp_policy", _stamp)
    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: "/nonexistent/codewhale")
    # The in-confinement DNS probe spawns its own child through the same
    # (patched) subprocess layer on hosts where confinement is available;
    # stub it so only the writer spawn is observed, on every platform.
    monkeypatch.setattr(worker_mod, "_dns_probe", lambda *a, **k: {})
    binding = TenantStoreBinding(
        tenant_key="key_fs2", store_dir=Path("/tmp/tenant-fs2"), bound_at=0.0
    )
    with pytest.raises(Exception) as caught:
        worker_mod.run_worker_job("build it", tmp_path, tenant_store=binding)
    if writable:
        assert "worker_policy_unwritable" not in str(caught.value)
    else:
        assert isinstance(caught.value, worker_mod.WorkerError)
        assert "worker_policy_unwritable" in str(caught.value)
        assert started == []
