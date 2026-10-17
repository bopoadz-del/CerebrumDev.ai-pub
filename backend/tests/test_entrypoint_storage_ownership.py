"""Boot never walks the whole storage tree to re-own it.

Live 2026-10-07: every cerebrumdev-backend task start sat silent for
221-346 s (12 of 12 log streams) between the container starting and
alembic's first line. docker-entrypoint.sh ran ``chown -R appuser`` over
$STORAGE_PATH -- every build workspace on network storage -- on every boot.
When Fargate replaced the task at 22:04Z, that walk was a 5.5-minute outage.

The recursive chown is for a disk that arrives root-owned (a fresh mount);
after that every file is written by appuser. These tests run the real
entrypoint under sh with ``id``/``stat``/``chown``/``setpriv`` stubbed, and
read which chown calls it made.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker-entrypoint.sh"
APP_UID = "10001"


def _sh() -> str:
    found = shutil.which("sh") or shutil.which("bash")
    if not found:
        pytest.fail("a POSIX sh is required to run docker-entrypoint.sh")
    return found


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _boot(tmp_path: Path, storage_uid: str) -> list[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "chown_calls.txt"
    _stub(
        bindir,
        "id",
        f'if [ "$1" = "-u" ] && [ -n "$2" ]; then echo {APP_UID}; else echo 0; fi',
    )
    _stub(bindir, "stat", f"echo {storage_uid}")
    _stub(bindir, "chown", f'echo "$*" >> "{calls.as_posix()}"')
    # The privilege drop is the end of the entrypoint; stop there.
    _stub(bindir, "setpriv", "exit 0")
    storage = tmp_path / "storage"
    env = dict(os.environ)
    env.update(
        {
            "PATH": bindir.as_posix() + os.pathsep + env.get("PATH", ""),
            "STORAGE_PATH": storage.as_posix(),
            "FACTORY_OUTPUTS_LINK": (tmp_path / "factory_outputs_link").as_posix(),
        }
    )
    script = ENTRYPOINT.read_text(encoding="utf-8").replace("\r\n", "\n")
    run = tmp_path / "entrypoint.sh"
    run.write_text(script, encoding="utf-8", newline="\n")
    proc = subprocess.run(
        [_sh(), run.as_posix(), "true"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    if not calls.is_file():
        return []
    return [line for line in calls.read_text(encoding="utf-8").splitlines() if line]


def test_a_disk_already_owned_by_appuser_is_not_walked(tmp_path):
    calls = _boot(tmp_path, storage_uid=APP_UID)
    recursive = [c for c in calls if c.startswith("-R")]
    assert recursive == [], f"boot re-owned the whole tree: {recursive}"
    # Only the directories the entrypoint itself may have created.
    assert calls, "the entrypoint's own directories must still be appuser's"


def test_a_root_owned_fresh_disk_is_re_owned_once(tmp_path):
    calls = _boot(tmp_path, storage_uid="0")
    recursive = [c for c in calls if c.startswith("-R")]
    assert len(recursive) == 1, calls
    assert recursive[0].endswith("storage")
