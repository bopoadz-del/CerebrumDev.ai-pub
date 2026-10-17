"""The gate mounts persistence where the IMAGE declares it, never where the gate guesses.

Live 2026-10-07 (build/plt_c0b1ad1f127e43f8, run 37578853125): the product's
Dockerfile declared ``STORAGE_PATH=/data`` and created /data owned by its
service account at build time. The gate overrode it with
``-e STORAGE_PATH=/app/storage -v <volume>:/app/storage`` -- a path the image
never had, so Docker created the volume root-owned, the service account could
not open its database, and the product died at boot
(``sqlite3.OperationalError: unable to open database file``). A named volume
mounted over a directory the image DOES have is initialised from that
directory, ownership included; that is the contract the floor states
(single_persistence_root), and the gate must honour it rather than impose its
own path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import storage_mount  # noqa: E402


def _inspect(env, workdir="/app"):
    return [{"Config": {"Env": env, "WorkingDir": workdir}}]


def test_the_declared_storage_root_is_mounted_not_overridden():
    got = storage_mount.resolve(_inspect(["PATH=/usr/bin", "STORAGE_PATH=/data"]))
    assert got == storage_mount.Mount(path="/data", declared=True)


def test_a_relative_declaration_resolves_against_the_images_workdir():
    got = storage_mount.resolve(_inspect(["STORAGE_PATH=./var/store"], workdir="/srv"))
    assert got == storage_mount.Mount(path="/srv/var/store", declared=True)


def test_a_declaration_naming_the_database_file_mounts_its_directory():
    # app.db accepts STORAGE_PATH naming the database file itself.
    got = storage_mount.resolve(_inspect(["STORAGE_PATH=/data/platform.db"]))
    assert got == storage_mount.Mount(path="/data", declared=True)


def test_no_declaration_falls_back_to_the_gate_default_and_says_so():
    got = storage_mount.resolve(_inspect(["PATH=/usr/bin"]))
    assert got == storage_mount.Mount(path=storage_mount.GATE_DEFAULT_ROOT, declared=False)


def test_an_unreadable_inspect_falls_back_and_says_so():
    assert storage_mount.resolve([]) == storage_mount.Mount(
        path=storage_mount.GATE_DEFAULT_ROOT, declared=False
    )


def test_the_cli_prints_docker_run_arguments(tmp_path):
    # Declared: mount only, no override (the image's own ENV stands).
    declared = subprocess.run(
        [sys.executable, str(HERE / "storage_mount.py"), "vol-x"],
        input=json.dumps(_inspect(["STORAGE_PATH=/data"])),
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert declared == ["-v", "vol-x:/data"]
    # Undeclared: the gate's default, and it must then SAY where it is.
    undeclared = subprocess.run(
        [sys.executable, str(HERE / "storage_mount.py"), "vol-y"],
        input=json.dumps(_inspect([])),
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert undeclared == [
        "-e", "STORAGE_PATH=" + storage_mount.GATE_DEFAULT_ROOT,
        "-v", "vol-y:" + storage_mount.GATE_DEFAULT_ROOT,
    ]


def test_the_workflow_never_imposes_a_storage_path_on_a_declaring_image():
    # Every container the gate starts takes its persistence arguments from
    # this resolver; no literal STORAGE_PATH override remains in the workflow.
    wf = (HERE.parent / "workflows" / "store-gate.yml").read_text(encoding="utf-8")
    assert "-e STORAGE_PATH=/app/storage" not in wf
    assert wf.count("STORAGE_MOUNT_ARGS") >= 3  # resolved once, used by both runs
