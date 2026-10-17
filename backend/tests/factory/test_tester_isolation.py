"""The generated suite must never share state with the build environment.

New-shape test for the stale-database defect found on the first live coder
build. The factory backend's own environment carries STORAGE_PATH (loaded
from backend/.env by the CLI), the tester subprocess inherits it, and the
generated conftest used ``setdefault`` -- so every rework round ran against
one shared database file. A table created by round N rejected round N+1's
columns (``no column named assigned_by``) and the rework loop spent its
whole budget on schema errors that no round's code had caused.
"""

from __future__ import annotations

from app.factory.build.roles import _CONFTEST


def _write_generated_conftest_tree(tmp_path):
    """The generated conftest imports app.migrations; isolation probes
    only write tests/conftest.py. Stub the product module so STORAGE_PATH
    forcing and the offline socket guard can still be judged alone."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "migrations.py").write_text(
        "def upgrade_head():\n    return None\n",
        encoding="utf-8",
    )


def test_generated_conftest_forces_storage_isolation(tmp_path):
    """Run the conftest with STORAGE_PATH already set, the way the tester
    subprocess actually runs it. The inherited value must lose.

    In a SUBPROCESS, deliberately: the conftest also patches socket.connect,
    and an in-process exec of it once poisoned the entire CI suite -- every
    later test that legitimately downloads failed with "offline suite:
    outbound connection refused"."""
    import os
    import subprocess
    import sys
    import textwrap

    _write_generated_conftest_tree(tmp_path)

    probe = textwrap.dedent(
        """
        import os, runpy
        runpy.run_path("tests/conftest.py")
        forced = os.environ["STORAGE_PATH"]
        assert forced != os.environ["EXPECT_NOT"], "inherited the build's storage"
        assert "platform-test-" in forced, forced
        print("STORAGE-ISOLATION-OK")
        """
    )
    inherited = str(tmp_path / "inherited-storage")
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "STORAGE_PATH": inherited, "EXPECT_NOT": inherited},
    )
    assert proc.returncode == 0, proc.stderr
    assert "STORAGE-ISOLATION-OK" in proc.stdout


def test_generated_conftest_blocks_outbound_network(tmp_path):
    """A round-12 handler "sent" a webhook to the open internet from a suite
    whose whole claim is offline; env-stripping cannot catch that. The
    conftest must refuse non-loopback connections and keep loopback open.
    Run in a subprocess: the guard patches socket globally."""
    import subprocess
    import sys
    import textwrap

    _write_generated_conftest_tree(tmp_path)

    probe = textwrap.dedent(
        """
        import runpy, socket, sys
        sys.argv = ["probe"]
        namespace = runpy.run_path("tests/conftest.py")

        # Outbound must be refused before any packet leaves.
        try:
            socket.create_connection(("93.184.216.34", 80), timeout=5)
        except OSError as exc:
            assert "offline suite" in str(exc), exc
        else:
            raise SystemExit("outbound connection was allowed")

        # Loopback must still work (TestClient-style local servers).
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.socket()
        client.connect(("127.0.0.1", listener.getsockname()[1]))
        client.close()
        listener.close()
        print("OFFLINE-GUARD-OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OFFLINE-GUARD-OK" in proc.stdout


def test_generated_conftest_registers_the_pilot_marker():
    """TESTER cannot write a repo-root pytest.ini; the marker lives here
    or ``pytest -m 'not pilot'`` warns and (with --strict-markers) fails."""
    assert "pytest_configure" in _CONFTEST
    assert "pilot:" in _CONFTEST


def test_gate_subprocesses_run_utf8(tmp_path):
    """A vendored block printing one checkmark died with a charmap
    UnicodeEncodeError under the Windows console codepage -- looking exactly
    like a block failure. Gate subprocesses must be UTF-8 regardless of
    platform."""
    import sys

    from app.factory.build.gates import _real_run

    proc = _real_run(
        [sys.executable, "-c", "print('\\u2713 ok')"],
        cwd=tmp_path,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
