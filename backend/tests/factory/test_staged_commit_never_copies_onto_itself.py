"""A staged commit never copies a file onto itself.

Live 2026-10-08 (98d3a356, rotation cycle 1, a rotation pick at WRITER): the
build thread crashed with "PosixPath('….staging-writer/blocks.lock.json') and
PosixPath('…/blocks.lock.json') are the same file". The coding agent works in
the staging tree and can leave there a link to the product's own file (a
symlink or a hard link into the destination); ``record_existing`` records every
file under staging, and ``commit()`` then asked shutil to copy the destination
file onto itself -- shutil.SameFileError, the build thread died, the product
was lost for a bookkeeping copy that had nothing to do.

The rule: a staged path that already IS the destination file is committed by
doing nothing, and the destination keeps its bytes.
"""

from __future__ import annotations

import os

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.workspace import RoleWorkspace

LOCK = "blocks.lock.json"
BODY = '{"pinned": "abc"}\n'


def _workspace(tmp_path):
    dest = tmp_path / "product"
    dest.mkdir()
    (dest / LOCK).write_text(BODY, encoding="utf-8")
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=tmp_path / ".product.staging-writer")
    return ws, dest


def _commit_keeps_the_destination(ws, dest):
    ws.record_existing()
    ws.commit()
    assert (dest / LOCK).read_text(encoding="utf-8") == BODY


def test_a_hard_link_to_the_destination_commits_as_a_no_op(tmp_path):
    ws, dest = _workspace(tmp_path)
    os.link(dest / LOCK, ws.workspace / LOCK)

    _commit_keeps_the_destination(ws, dest)


def test_a_symlink_to_the_destination_commits_as_a_no_op(tmp_path):
    ws, dest = _workspace(tmp_path)
    try:
        os.symlink(dest / LOCK, ws.workspace / LOCK)
    except (OSError, NotImplementedError):
        pytest.skip(reason="this host cannot create symlinks")

    _commit_keeps_the_destination(ws, dest)


def test_a_real_staged_write_still_lands(tmp_path):
    ws, dest = _workspace(tmp_path)
    ws.write_text("app/main.py", "app = None\n")

    ws.commit()

    assert (dest / "app" / "main.py").read_text(encoding="utf-8") == "app = None\n"
    assert (dest / LOCK).read_text(encoding="utf-8") == BODY


def test_copy_file_onto_the_same_file_is_a_no_op(tmp_path):
    dest = tmp_path / "product"
    dest.mkdir()
    (dest / LOCK).write_text(BODY, encoding="utf-8")
    ws = RoleWorkspace(BuildRole.CLONER, dest)

    ws.copy_file(dest / LOCK, LOCK)

    assert (dest / LOCK).read_text(encoding="utf-8") == BODY
