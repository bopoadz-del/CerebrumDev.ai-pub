"""F13/F26: block stdout and gate children must survive a cp1252 console.

A vendored block that prints one checkmark used to die with a charmap
UnicodeEncodeError. Dispatch then swallowed that into a generic error
envelope that looked like a domain refusal (F13). This file is the
smallest subset that would have caught that, plus the existing gate
UTF-8 force.

This is not a claim that Linux is Windows. The control child sets
PYTHONIOENCODING=cp1252 (Python honors that on any host). Official F26
parity is the windows-latest job in .github/workflows/ci.yml.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.factory.build.gates import _real_run
from app.factory.build.roles import _DISPATCH_RUNTIME


def _hostile_cp1252_env() -> dict[str, str]:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    return env


def _raw_checkmark_child(tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "print('\\u2713')"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_hostile_cp1252_env(),
    )


def test_dispatch_runtime_forces_utf8_stdio():
    assert "_force_utf8_stdio" in _DISPATCH_RUNTIME
    assert 'os.environ["PYTHONIOENCODING"] = "utf-8"' in _DISPATCH_RUNTIME
    assert "UnicodeEncodeError on block stdout" in _DISPATCH_RUNTIME


def test_inherited_cp1252_rejects_checkmark_without_utf8_force(tmp_path):
    """Control: PYTHONIOENCODING=cp1252 must actually reject U+2713.

    If this host ignores that override, the Windows job is still the F26
    path — do not treat a UTF-8 Linux console as Windows.
    """
    proc = _raw_checkmark_child(tmp_path)
    hostile = proc.returncode != 0 and (
        "UnicodeEncodeError" in (proc.stderr or "")
        or "charmap" in (proc.stderr or "")
    )
    if sys.platform == "win32":
        assert hostile, (
            "windows-latest must reject U+2713 under PYTHONIOENCODING=cp1252; "
            f"rc={proc.returncode} stderr={proc.stderr!r}"
        )
        return
    if not hostile:
        pytest.skip(
            reason="host ignored PYTHONIOENCODING=cp1252; "
            "F26 stays on the windows-latest job"
        )


def test_gate_run_overrides_inherited_cp1252(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    proc = _real_run(
        [sys.executable, "-c", "print('\\u2713 ok')"],
        cwd=tmp_path,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_dispatch_emoji_print_is_not_swallowed_as_a_domain_error(tmp_path):
    """F13: a block that prints emoji/checkmark must return its real result.

    Run execute() in a child that inherits cp1252. UTF-8 force on block
    stdout must make the print succeed. A swallowed UnicodeEncodeError
    would look like a block refusal.
    """
    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(_DISPATCH_RUNTIME, encoding="utf-8")
    block = root / "vendor" / "blocks" / "printer"
    block.mkdir(parents=True)
    (block / "block.py").write_text(
        "def run(**kwargs):\n"
        "    print('\\u2713 ok')\n"
        "    print('\\U0001f698')\n"
        "    return {'status': 'ok', 'printed': True}\n",
        encoding="utf-8",
    )
    probe = textwrap.dedent(
        """
        import json
        import sys
        sys.path.insert(0, ".")
        from app.dispatch import execute
        out = execute("printer", {"x": 1}, action="run")
        # Marker on its own line so block prints (checkmark/emoji) do not
        # poison JSON parse of the result.
        sys.stderr.write("F13_RESULT=" + json.dumps(out) + "\\n")
        if out.get("status") != "ok" or out.get("printed") is not True:
            raise SystemExit("dispatch did not return the block result: " + repr(out))
        if "UnicodeEncodeError" in str(out):
            raise SystemExit("UnicodeEncodeError was swallowed: " + repr(out))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_hostile_cp1252_env(),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    marker = "F13_RESULT="
    lines = [line for line in (proc.stderr or "").splitlines() if line.startswith(marker)]
    assert lines, proc.stderr
    out = json.loads(lines[-1][len(marker) :])
    assert out["status"] == "ok"
    assert out["printed"] is True
    assert "UnicodeEncodeError" not in str(out)
