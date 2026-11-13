"""F-S2a / F-S2a-2: the writer's pytest tool policy -- a refusal mechanism, not a prompt line.

F-S2a-2: the policy used to ride the writer's env (PYTEST_ADDOPTS, PYTHONPATH,
a root variable). The writer CLI's shell rebuilds its children's env from its
own allowlist, so the plugin never loaded (pilot: suite_runs=20
suite_denied=0). It is now loaded by the Factory's rootdir bootstrap from a
file stamped into the workspace for one pass.

Every rule is pinned by two inputs that must disagree: the targeted file vs
the whole tests/ tree, a full env vs the CLI's stripped env, a stamped pass vs
the Factory's own run after the stamp is removed, zero logged refusals vs two,
a readable log vs a missing one.
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
from app.factory.build.builds_push import is_exported
from app.factory.build.codewhale_worker import (
    pytest_runs_in,
    rounds_summary_line,
    suite_denied_count,
)
from app.factory.build.pytest_policy import (
    CONFTEST_LOADER,
    DENIALS_FILE,
    LOADER_MARKER,
    POLICY_DIR_REL,
    SEEN_FILE,
    loader_present,
    policy_binding,
    policy_dir,
    read_denials,
    read_seen,
    remove_policy,
    stamp_policy,
)
from app.factory.build.roles_constants import _CONFTEST, CONFTEST_REL
from app.factory.build.writer_behaviour import SELF_CHECK_REL, render_self_check

#: What the writer CLI's shell hands a child on Linux: its allowlist only
#: (PATH, HOME, locale ...). Windows needs SYSTEMROOT to start Python at all.
_CLI_SHELL_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT")


def _workspace(root: Path, bootstrap: str = _CONFTEST) -> None:
    """One changed source with a test that names it, one unrelated test, and
    the Factory's rootdir bootstrap."""
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_widget.py").write_text(
        "def test_widget():\n    assert 'widget'\n", encoding="utf-8"
    )
    (root / "tests" / "test_other.py").write_text(
        "def test_other():\n    assert True\n", encoding="utf-8"
    )
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / CONFTEST_REL).write_text(bootstrap, encoding="utf-8")
    stamp = root / SELF_CHECK_REL
    stamp.parent.mkdir(parents=True)
    stamp.write_text(render_self_check(), encoding="utf-8")
    past = time.time() - 600
    os.utime(stamp, (past, past))
    for rel in (
        "app/__init__.py",
        "tests/test_widget.py",
        "tests/test_other.py",
        "pytest.ini",
        CONFTEST_REL,
    ):
        os.utime(root / rel, (past - 60, past - 60))
    # The pass's edit: newer than the stamp.
    (root / "app" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")


def _run_pytest(root: Path, stripped: bool, *args: str) -> subprocess.CompletedProcess:
    if stripped:
        env = {k: os.environ[k] for k in _CLI_SHELL_KEYS if k in os.environ}
    else:
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.parametrize("stripped", [False, True])
@pytest.mark.parametrize(
    "selection,refused",
    [(["tests/test_widget.py"], False), (["tests"], True), ([], True)],
)
def test_writer_pytest_outside_targeted_set_is_refused_in_any_env(
    tmp_path, stripped, selection, refused
):
    """The bootstrap loads the policy whether or not the env survived the
    CLI's shell: a full env and the stripped env give the same verdict."""
    _workspace(tmp_path)
    stamp_policy(tmp_path)
    proc = _run_pytest(tmp_path, stripped, *selection)
    denials = read_denials(tmp_path)
    seen = read_seen(tmp_path)
    assert len(seen) == 1, proc.stdout + proc.stderr
    assert seen[0]["refused"] is refused
    if refused:
        assert proc.returncode == 4, proc.stdout + proc.stderr
        assert "factory policy" in proc.stderr
        assert "tests/test_widget.py" in proc.stderr
        assert len(denials) == 1
        assert denials[0]["targeted"] == ["tests/test_widget.py"]
    else:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert denials == []


@pytest.mark.parametrize("stamped,refused", [(True, True), (False, False)])
def test_policy_binds_only_while_the_pass_stamp_exists(tmp_path, stamped, refused):
    """The Factory's own suite runs happen after the stamp is removed and are
    never refused, from the same bootstrap."""
    _workspace(tmp_path)
    stamp_policy(tmp_path)
    if not stamped:
        remove_policy(tmp_path)
    proc = _run_pytest(tmp_path, True, "tests")
    assert (proc.returncode == 4) is refused, proc.stdout + proc.stderr
    assert policy_dir(tmp_path).exists() is stamped


@pytest.mark.parametrize("bootstrap,bound", [(_CONFTEST, True), ("", False)])
def test_only_the_factory_bootstrap_loads_the_policy(tmp_path, bootstrap, bound):
    """A root conftest without the loader leaves the run unjudged: the seen
    log stays empty, which the receipt reads as unbound."""
    _workspace(tmp_path, bootstrap)
    stamp_policy(tmp_path)
    proc = _run_pytest(tmp_path, True, "tests")
    assert (proc.returncode == 4) is bound, proc.stdout + proc.stderr
    seen = read_seen(tmp_path)
    assert len(seen) == (1 if bound else 0)
    assert policy_binding(1, seen) == ("bound" if bound else "unbound")


@pytest.mark.parametrize("prior", [1, 3])
def test_stamp_writes_the_plugin_and_resets_this_pass_logs(tmp_path, prior):
    """A later pass starts from empty logs whatever the previous pass left."""
    target = stamp_policy(tmp_path)
    assert target == tmp_path.joinpath(*POLICY_DIR_REL.split("/"))
    assert (target / "factory_pytest_policy.py").is_file()
    for name in (DENIALS_FILE, SEEN_FILE):
        (target / name).write_text('{"selection": []}\n' * prior, encoding="utf-8")
    assert len(read_denials(tmp_path)) == prior
    assert len(read_seen(tmp_path)) == prior
    stamp_policy(tmp_path)
    assert read_denials(tmp_path) == []
    assert read_seen(tmp_path) == []


@pytest.mark.parametrize(
    "text,count",
    [(None, None), ("", 0), ('{"a": 1}\n\nnot json\n', 2)],
)
def test_denial_log_unreadable_is_not_zero(tmp_path, text, count):
    if text is not None:
        policy_dir(tmp_path).mkdir(parents=True)
        (policy_dir(tmp_path) / DENIALS_FILE).write_text(text, encoding="utf-8")
    assert suite_denied_count(read_denials(tmp_path)) == count
    shown = "-" if count is None else str(count)
    assert f"suite_denied={shown}" in rounds_summary_line({"suite_denied": count})


@pytest.mark.parametrize(
    "line,entry",
    [('{"selection": []}', {"selection": []}), ("[1]", {"unparsed": "[1]"})],
)
def test_log_entries_are_always_objects(tmp_path, line, entry):
    policy_dir(tmp_path).mkdir(parents=True)
    (policy_dir(tmp_path) / SEEN_FILE).write_text(line + "\n", encoding="utf-8")
    assert read_seen(tmp_path) == [entry]


@pytest.mark.parametrize(
    "runs,seen,binding",
    [
        (0, [], "idle"),
        (2, [], "unbound"),
        (2, [{"refused": False}], "bound"),
        (0, [{"refused": True}], "bound"),
        (2, None, None),
    ],
)
def test_policy_binding_names_a_policy_that_did_not_load(runs, seen, binding):
    assert policy_binding(runs, seen) == binding
    shown = "-" if binding is None else binding
    assert f"policy={shown}" in rounds_summary_line({"policy": binding})


@pytest.mark.parametrize(
    "command,runs",
    [("ls -la", 0), ("python -m pytest tests/a.py && pytest tests", 2)],
)
def test_pytest_runs_in_counts_every_invocation(command, runs):
    event = {"type": "tool_use", "name": "bash", "input": {"command": command}}
    assert pytest_runs_in(event) == runs
    assert pytest_runs_in({**event, "type": "tool_result"}) == 0


@pytest.mark.parametrize(
    "text,present",
    [(None, None), ("import os\n", False), (_CONFTEST, True)],
)
def test_loader_present_reads_the_rootdir_bootstrap(tmp_path, text, present):
    if text is not None:
        (tmp_path / CONFTEST_REL).write_text(text, encoding="utf-8")
    assert loader_present(tmp_path) is present


def test_the_shipped_bootstrap_carries_the_loader_once():
    assert _CONFTEST.count(LOADER_MARKER) == 1
    assert _CONFTEST.endswith(CONFTEST_LOADER)
    assert "os.environ" not in CONFTEST_LOADER


@pytest.mark.parametrize(
    "existing,rerendered",
    [("# an older bootstrap\n", True), (_CONFTEST, False)],
)
def test_prestamp_rerenders_a_bootstrap_without_the_loader(tmp_path, existing, rerendered):
    from app.factory.build.factory_owned import prestamp_late_files

    (tmp_path / CONFTEST_REL).write_text(existing, encoding="utf-8")
    changed = prestamp_late_files(tmp_path)
    assert (CONFTEST_REL in changed) is rerendered
    assert loader_present(tmp_path) is True


@pytest.mark.parametrize(
    "rel,shipped",
    [
        (POLICY_DIR_REL + "/" + SEEN_FILE, False),
        (".factory/other.json", True),
        ("app/pytest_policy/x.py", True),
    ],
)
def test_the_stamp_never_ships(rel, shipped):
    assert is_exported(Path(rel)) is shipped


def _fake_job(monkeypatch, tmp_path, command, seen_lines, refusals):
    """Run one writer pass with a fake CLI that runs ``command`` and leaves
    the given policy logs; return (receipt, stamp-existed-during-pass)."""
    from app.factory.build.tenant_bind import TenantStoreBinding

    during: dict = {}
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
        "input": {"command": command},
    }
    stdout = json.dumps(tool) + "\n" + json.dumps(meta) + "\n"

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            env = kwargs.get("env") or {}
            during["env"] = dict(env)
            target = policy_dir(tmp_path)
            during["stamped"] = (target / "factory_pytest_policy.py").is_file()
            with (target / SEEN_FILE).open("a", encoding="utf-8") as fh:
                for _ in range(seen_lines):
                    fh.write(json.dumps({"selection": []}) + "\n")
            with (target / DENIALS_FILE).open("a", encoding="utf-8") as fh:
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
    return receipt, during


@pytest.mark.parametrize(
    "command,seen_lines,refusals,policy",
    [
        ("ls", 0, 0, "idle"),
        ("pytest tests", 1, 1, "bound"),
        ("pytest tests && pytest", 0, 0, "unbound"),
        ("pytest tests && pytest", 2, 2, "bound"),
    ],
)
def test_run_worker_job_reports_policy_and_refusals_then_drops_the_stamp(
    monkeypatch, tmp_path, command, seen_lines, refusals, policy
):
    """End to end: the stamp exists while the writer runs, its logs come back
    on the receipt, and it is gone when the pass ends. Nothing rides the env."""
    receipt, during = _fake_job(monkeypatch, tmp_path, command, seen_lines, refusals)
    assert during["stamped"] is True
    assert "PYTEST_ADDOPTS" not in during["env"]
    assert receipt.rounds["suite_denied"] == refusals
    assert receipt.rounds["policy"] == policy
    assert f"policy={policy}" in rounds_summary_line(receipt.rounds)
    assert not policy_dir(tmp_path).exists()


@pytest.mark.parametrize(
    "bootstrap,named",
    [(None, False), (_CONFTEST, False), ("# no loader\n", True)],
)
def test_bootstrap_without_the_loader_fails_the_dispatch_named(
    monkeypatch, tmp_path, bootstrap, named
):
    """A Factory bootstrap present without the loader would be a silent
    policy-free pass: it is a named dispatch failure instead."""
    if bootstrap is not None:
        (tmp_path / CONFTEST_REL).write_text(bootstrap, encoding="utf-8")
    if named:
        with pytest.raises(worker_mod.WorkerError) as caught:
            _fake_job(monkeypatch, tmp_path, "ls", 0, 0)
        assert "worker_policy_unbound" in str(caught.value)
        assert not policy_dir(tmp_path).exists()
    else:
        receipt, during = _fake_job(monkeypatch, tmp_path, "ls", 0, 0)
        assert during["stamped"] is True
        assert receipt.rounds["policy"] == "idle"


@pytest.mark.parametrize("writable", [True, False])
def test_unwritable_policy_fails_the_dispatch_named(monkeypatch, tmp_path, writable):
    """A writer without the policy is not the writer ordered: an unwritable
    policy dir is a named dispatch failure, never a silent policy-free pass."""
    from app.factory.build import pytest_policy
    from app.factory.build.tenant_bind import TenantStoreBinding

    started: list = []

    def _stamp(root):
        if not writable:
            raise OSError("read-only")
        return policy_dir(root)

    class _Started(Exception):
        pass

    def _popen(argv, **kwargs):
        started.append(argv)
        raise _Started()

    monkeypatch.setattr(pytest_policy, "stamp_policy", _stamp)
    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: "/nonexistent/codewhale")
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
