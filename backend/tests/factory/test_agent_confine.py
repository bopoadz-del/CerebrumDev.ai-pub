"""Coding-agent children get an allowlisted environment and a real Landlock ruleset.

Each case is a separate input. Denial is observed from a child process, not from
the wrapper's keyword arguments.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build import agent_confine
from app.factory.build.agent_confine import (
    ConfinementUnavailable,
    confinement_popen_kwargs,
    narrow_rw_roots,
    scoped_child_env,
)

_READER = """
import os, sys, subprocess
mode, root, other, name = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

def denied(err):
    sys.stdout.write("DENIED %s\\n" % err)

try:
    if mode == "inside":
        data = open(os.path.join(root, "own"), "r", encoding="utf-8").read()
    elif mode == "traverse":
        data = open(os.path.join(root, "..", os.path.basename(other), name), "r", encoding="utf-8").read()
    elif mode == "symlink":
        data = open(os.path.join(root, "via-link"), "r", encoding="utf-8").read()
    elif mode == "absolute":
        data = open(os.path.join(other, name), "r", encoding="utf-8").read()
    elif mode == "proc":
        data = open("/proc/self/root" + os.path.join(other, name), "r", encoding="utf-8").read()
    elif mode == "grandchild":
        proc = subprocess.run(
            [sys.executable, os.path.join(root, "reader.py"), "absolute", root, other, name],
            capture_output=True, text=True,
        )
        sys.stdout.write(proc.stdout)
        raise SystemExit(0)
    elif mode == "fd":
        data = os.read(int(os.environ["INHERITED_FD"]), 64).decode("utf-8")
    else:
        raise SystemExit(2)
except OSError as exc:
    denied(exc.errno)
    raise SystemExit(0)
sys.stdout.write("LEAK " + data)
"""


def _layout(tmp: Path, left_body: str, right_body: str, sibling: str):
    base = tmp / "sessions" / "one"
    left = base / "build"
    right = base / sibling
    left.mkdir(parents=True)
    right.mkdir()
    (left / "own").write_text(left_body, encoding="utf-8")
    (right / "marker").write_text(right_body, encoding="utf-8")
    (left / "via-link").symlink_to(right / "marker")
    (left / "reader.py").write_text(_READER, encoding="utf-8")
    return left, right


def _spawn(left: Path, right: Path, mode: str, *, handled: int = 0):
    env = scoped_child_env(
        {"PATH": os.environ.get("PATH", "")},
        {},
        home=left,
        tmp=left,
    )
    fd = None
    pass_fds: tuple[int, ...] = ()
    if mode == "fd":
        fd = os.open(right / "marker", os.O_RDONLY)
        os.set_inheritable(fd, True)
        env["INHERITED_FD"] = str(fd)
        pass_fds = (fd,)
    kwargs = confinement_popen_kwargs(left, sys.executable, handled=handled)
    try:
        return subprocess.run(
            [sys.executable, str(left / "reader.py"), mode, str(left), str(right), "marker"],
            cwd=str(left),
            env=env,
            capture_output=True,
            text=True,
            pass_fds=pass_fds,
            check=False,
            **kwargs,
        )
    finally:
        if fd is not None:
            os.close(fd)


@pytest.mark.parametrize(
    "secret,value",
    [
        ("CEREBRUM_DEV_API_KEY", "dummy-admin"),
        ("ACCOUNTS_DATABASE_URL", "dummy-db"),
    ],
)
def test_scoped_env_drops_service_secrets_and_keeps_the_writer_key(secret, value):
    parent = {secret: value, "PATH": "/usr/bin", "DEEPSEEK_API_KEY": "stale-writer"}
    env = scoped_child_env(
        parent,
        {"DEEPSEEK_API_KEY": "fresh-writer", "KIMI_MODEL_API_KEY": "fresh-writer"},
        home="/srv/build-a",
        tmp="/srv/build-a",
    )
    assert env["DEEPSEEK_API_KEY"] == "fresh-writer"
    assert env["KIMI_MODEL_API_KEY"] == "fresh-writer"
    assert secret not in env
    assert "stale-writer" not in env.values()


@pytest.mark.parametrize(
    "provider_key,secret",
    [
        ("DEEPSEEK_API_KEY", "DATA_ENCRYPTION_KEY"),
        ("ACME_API_KEY", "SMOKE_GATE_TOKEN"),
    ],
)
def test_provider_stamp_is_scoped_and_chat_key_is_not_forwarded(provider_key, secret):
    parent = {
        provider_key: "stale-provider",
        secret: "dummy-service",
        "CEREBRUM_CHAT_LLM_API_KEY": "dummy-chat",
        "STRIPE_SECRET_KEY": "dummy-stripe",
        "RESEND_API_KEY": "dummy-mail",
        "OPENROUTER_API_KEY": "dummy-router",
        "PATH": "/usr/bin",
    }
    env = scoped_child_env(
        parent,
        {"CODEWHALE_API_KEY": "resolved-writer", provider_key: "resolved-writer"},
        home="/srv/build-b",
        tmp="/srv/build-b",
        provider_key=provider_key,
    )
    assert env["CODEWHALE_API_KEY"] == "resolved-writer"
    assert env[provider_key] == "resolved-writer"
    assert "CEREBRUM_CHAT_LLM_API_KEY" not in env
    assert secret not in env
    assert "STRIPE_SECRET_KEY" not in env
    assert "OPENROUTER_API_KEY" not in env


@pytest.mark.parametrize(
    "mode,secret",
    [
        ("traverse", "alpha-secret"),
        ("symlink", "beta-secret"),
    ],
)
def test_sibling_traversal_and_symlink_are_denied(tmp_path, mode, secret):
    left, right = _layout(tmp_path, "own-marker", secret, "other-build")
    proc = _spawn(left, right, mode)
    assert proc.stdout.split()[0] == "DENIED"
    assert secret not in proc.stdout
    assert "LEAK" not in proc.stdout


@pytest.mark.parametrize(
    "mode,secret",
    [
        ("absolute", "gamma-secret"),
        ("proc", "delta-secret"),
    ],
)
def test_absolute_and_proc_root_paths_are_denied(tmp_path, mode, secret):
    left, right = _layout(tmp_path, "own-marker", secret, "other-build")
    proc = _spawn(left, right, mode)
    assert proc.stdout.split()[0] == "DENIED"
    assert secret not in proc.stdout


@pytest.mark.parametrize(
    "mode,secret",
    [
        ("grandchild", "epsilon-secret"),
        ("fd", "zeta-secret"),
    ],
)
def test_grandchild_and_inherited_fd_cannot_read_the_sibling(tmp_path, mode, secret):
    left, right = _layout(tmp_path, "own-marker", secret, "other-build")
    proc = _spawn(left, right, mode)
    assert proc.stdout.split()[0] == "DENIED"
    assert secret not in proc.stdout
    assert "LEAK" not in proc.stdout


@pytest.mark.parametrize("body", ["inside-alpha", "inside-beta"])
def test_a_file_inside_the_build_root_stays_readable(tmp_path, body):
    left, right = _layout(tmp_path, body, "other", "other-build")
    proc = _spawn(left, right, "inside")
    assert proc.stdout == "LEAK " + body


@pytest.mark.parametrize("handled", [1 << 62, 1 << 63])
def test_a_ruleset_the_kernel_rejects_exits_before_the_read(tmp_path, handled):
    left, right = _layout(tmp_path, "own", "hidden-secret", "other-build")
    proc = _spawn(left, right, "absolute", handled=handled)
    assert proc.returncode == 126
    assert "hidden-secret" not in proc.stdout
    assert "LEAK" not in proc.stdout


@pytest.mark.parametrize("sibling", ["staging-a", "staging-b"])
def test_only_the_build_and_a_same_parent_sibling_are_writable_roots(tmp_path, sibling):
    product = tmp_path / "sess" / "product"
    extra = tmp_path / "sess" / sibling
    product.mkdir(parents=True)
    extra.mkdir()
    roots = narrow_rw_roots(product, [extra, product.parent, tmp_path])
    assert roots[0] == str(product.resolve())
    assert str(extra.resolve()) in roots
    assert str(product.parent.resolve()) not in roots
    assert str(tmp_path.resolve()) not in roots


@pytest.mark.parametrize("broad", ["/", "/tmp"])
def test_a_broad_filesystem_root_is_refused(broad):
    with pytest.raises(ConfinementUnavailable):
        narrow_rw_roots(broad)


@pytest.mark.parametrize("log", ["/tmp/argv-a.log", "/tmp/argv-b.log"])
def test_scoped_env_passes_the_argv_log_through(log):
    env = scoped_child_env({"CODE_CLI_ARGV_LOG": log, "PATH": "/usr/bin"}, {})
    assert env["CODE_CLI_ARGV_LOG"] == log


@pytest.mark.parametrize("force", ["1", "0"])
def test_scoped_env_passes_the_force_http1_flag(force):
    env = scoped_child_env({"CODEWHALE_FORCE_HTTP1": force, "PATH": "/usr/bin"}, {})
    assert env["CODEWHALE_FORCE_HTTP1"] == force


class _FakeStat:
    def __init__(self, mode):
        self.st_mode = mode


@pytest.mark.parametrize("retval,expected", [(-1, 0), (3, 3)])
def test_landlock_abi_returns_zero_below_one_and_value_above(monkeypatch, retval, expected):
    def fake(*_args):
        return retval

    monkeypatch.setattr(agent_confine, "_syscall", lambda: fake)
    assert agent_confine.landlock_abi() == expected


@pytest.mark.parametrize("abi,raises", [(0, True), (3, False)])
def test_require_landlock_raises_below_one_and_returns_above(monkeypatch, abi, raises):
    monkeypatch.setattr(agent_confine, "landlock_abi", lambda: abi)
    if raises:
        with pytest.raises(ConfinementUnavailable):
            agent_confine.require_landlock()
    else:
        assert agent_confine.require_landlock() == abi


@pytest.mark.parametrize(
    "stat_result,expected",
    [
        (stat.S_IFCHR | 0o600, agent_confine._DEV),
        (stat.S_IFREG | 0o644, agent_confine._RX),
        (None, agent_confine._RX),
    ],
)
def test_rights_for_char_device_gets_dev_and_others_rx(monkeypatch, stat_result, expected):
    def fake_stat(_path, **_kwargs):
        if stat_result is None:
            raise OSError("missing")
        return _FakeStat(stat_result)

    monkeypatch.setattr(os, "stat", fake_stat)
    assert agent_confine._rights_for("/fake/path") == expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("HTTPS_PROXY", "http://proxy.internal:3128"),
        ("http_proxy", "http://proxy.internal:3128"),
        ("NO_PROXY", ".internal,localhost"),
        ("RES_OPTIONS", "attempts:2 timeout:1"),
    ],
)
def test_scoped_env_passes_resolver_and_proxy_through(key, value):
    """The writer child must inherit resolver/proxy config or its DNS lookup
    fails deterministically while the CLONER (full env) resolves fine.
    Secrets are still dropped. Two inputs (proxy/resolver keys)."""
    parent = {key: value, "PATH": "/usr/bin", "SMOKE_GATE_TOKEN": "dummy-secret"}
    env = scoped_child_env(parent, {}, home="/srv/x", tmp="/srv/x")
    assert env[key] == value
    assert "SMOKE_GATE_TOKEN" not in env
