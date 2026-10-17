"""One platform = one branch: archive, restore, release, start-over tagging.

Runs every ref operation against a real git remote (a local bare repo), the
same commands the Factory runs against cerebrum-builds.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from app.factory.build import platform_branch as pb
from app.factory.build.platform_identity import (
    branch_of_record,
    is_platform_id,
    mint_platform_id,
)


def _git(args, cwd):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture()
def remote(tmp_path):
    bare = tmp_path / "builds.git"
    _git(["init", "-q", "--bare", str(bare)], tmp_path)
    return str(bare)


def _push_tree(remote: str, branch: str, files: dict, tmp: Path) -> str:
    work = tmp / ("w-" + branch.replace("/", "_"))
    work.mkdir()
    _git(["init", "-q"], work)
    for rel, text in files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    _git(["add", "-A"], work)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed"], work)
    _git(["push", "-q", remote, f"HEAD:refs/heads/{branch}"], work)
    return _git(["rev-parse", "HEAD"], work)


def _tree(path: Path) -> dict:
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(path).parts
    }


def test_platform_id_is_minted_not_named():
    a, b = mint_platform_id(), mint_platform_id()
    assert is_platform_id(a) and is_platform_id(b) and a != b
    assert not is_platform_id("product")
    assert branch_of_record(a) == "build/" + a


def test_archive_then_continue_restores_the_tree_byte_equal(remote, tmp_path):
    pid = mint_platform_id()
    files = {"app/main.py": "print('platform')\n", "docs/notes.md": "kept\n"}
    sha = _push_tree(remote, branch_of_record(pid), files, tmp_path)

    archived = pb.archive_branch(remote, pid, day=date(2026, 1, 2))
    assert archived is not None and archived.deleted
    assert archived.tag == f"archive/{pid}/2026-01-02"
    assert pb.branch_head(remote, pid) is None  # branch gone ...
    assert pb.tag_sha(remote, archived.tag) == sha  # ... its head kept by the tag

    restored = pb.restore_branch(remote, pid)  # what "Continue" does first
    assert restored == sha
    out = tmp_path / "restored"
    pb.clone_branch(remote, pid, out)
    original = tmp_path / "orig"
    original.mkdir()
    for rel, text in files.items():
        p = original / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    assert _tree(out) == _tree(original)


def test_a_head_is_never_deleted_without_a_tag_holding_it(remote, tmp_path, monkeypatch):
    pid = mint_platform_id()
    _push_tree(remote, branch_of_record(pid), {"a.txt": "x\n"}, tmp_path)
    # The tag read-back disagrees with the head: the delete must not run.
    real = pb.tag_sha
    calls = {"n": 0}

    def lying_tag_sha(r, tag):
        calls["n"] += 1
        return "0" * 40 if calls["n"] >= 3 else real(r, tag)

    monkeypatch.setattr(pb, "tag_sha", lying_tag_sha)
    with pytest.raises(pb.PlatformBranchError):
        pb.archive_branch(remote, pid, day=date(2026, 1, 3))
    monkeypatch.setattr(pb, "tag_sha", real)
    assert pb.branch_head(remote, pid) is not None


def test_start_over_tags_the_head_and_keeps_the_branch(remote, tmp_path):
    pid = mint_platform_id()
    sha = _push_tree(remote, branch_of_record(pid), {"a.txt": "v1\n"}, tmp_path)
    kept = pb.tag_head_for_archive(remote, pid, day=date(2026, 2, 1))
    assert kept is not None and not kept.deleted and kept.sha == sha
    assert pb.branch_head(remote, pid) == sha  # nothing deleted
    # A second Start over the same day keeps a distinct tag, never moves the first.
    _git(["init", "-q", str(tmp_path / "w2")], tmp_path)
    w2 = tmp_path / "w2"
    _git(["fetch", "-q", remote, branch_of_record(pid)], w2)
    _git(["checkout", "-q", "FETCH_HEAD"], w2)
    (w2 / "a.txt").write_text("v2\n", encoding="utf-8")
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "v2"], w2)
    _git(["push", "-q", remote, f"HEAD:refs/heads/{branch_of_record(pid)}"], w2)
    again = pb.tag_head_for_archive(remote, pid, day=date(2026, 2, 1))
    assert again.tag == f"archive/{pid}/2026-02-01.2"
    assert pb.tag_sha(remote, kept.tag) == sha


def test_release_tag_points_at_the_certified_head(remote, tmp_path):
    pid = mint_platform_id()
    sha = _push_tree(remote, branch_of_record(pid), {"a.txt": "x\n"}, tmp_path)
    tag, at = pb.tag_release(remote, pid, "1")
    assert tag == f"release/{pid}/1" and at == sha


def test_no_restore_when_nothing_was_archived(remote):
    assert pb.restore_branch(remote, mint_platform_id()) is None
