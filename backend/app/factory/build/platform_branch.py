"""The branch of record: create, tag, archive, restore.

Every operation is plain git against the cerebrum-builds remote (the token
URL in production, any git URL in a test), so the same code runs against
GitHub and a local bare repo.

Two invariants hold BY CONSTRUCTION here, the only module that moves a
platform's refs:

* a head is never deleted without a tag that points at that exact sha
  (``archive_branch`` tags, re-reads the tag, compares, and only then deletes);
* a platform has exactly one branch, ``build/<platform_id>`` -- every write
  goes to ``branch_of_record``; nothing here invents a sibling.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Mapping, Optional

from app.factory.build.platform_identity import (
    archive_tag,
    archive_tag_prefix,
    branch_of_record,
    release_tag,
)


class PlatformBranchError(RuntimeError):
    """A branch operation that could not be completed safely."""


def _git(args, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or Path.cwd()),
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def builds_remote(env: Optional[Mapping[str, str]] = None) -> str:
    """The cerebrum-builds remote the Factory pushes to."""
    from app.factory.build.branch_attach import _remote_url

    return _remote_url(env if env is not None else os.environ)


def _ls_remote(remote: str, pattern: str) -> List[tuple]:
    proc = _git(["ls-remote", remote, pattern])
    if proc.returncode != 0:
        raise PlatformBranchError(f"ls-remote failed: {(proc.stderr or '')[-300:]}")
    out = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


def ref_sha(remote: str, ref: str) -> Optional[str]:
    """The sha ``ref`` (full name, ``refs/...``) points at, or None."""
    for sha, name in _ls_remote(remote, ref):
        if name == ref:
            return sha
    return None


def branch_head(remote: str, platform_id: str) -> Optional[str]:
    return ref_sha(remote, "refs/heads/" + branch_of_record(platform_id))


def tag_sha(remote: str, tag: str) -> Optional[str]:
    # An annotated tag lists its peeled commit as ``<tag>^{}``; prefer it.
    rows = dict((name, sha) for sha, name in _ls_remote(remote, "refs/tags/" + tag + "*"))
    return rows.get("refs/tags/" + tag + "^{}") or rows.get("refs/tags/" + tag)


def _push_ref(remote: str, sha: str, ref: str) -> None:
    with tempfile.TemporaryDirectory(prefix="cerebrum-ref-") as tmp:
        work = Path(tmp)
        _git(["init", "-q"], work)
        fetched = _git(["fetch", "-q", remote, sha], work)
        if fetched.returncode != 0:
            raise PlatformBranchError(f"fetch {sha[:12]} failed: {(fetched.stderr or '')[-300:]}")
        pushed = _git(["push", "-q", remote, f"{sha}:{ref}"], work)
        if pushed.returncode != 0:
            raise PlatformBranchError(f"push {ref} failed: {(pushed.stderr or '')[-300:]}")


def create_tag(remote: str, sha: str, tag: str) -> str:
    """Point ``tag`` at ``sha``; an existing tag must already point there."""
    existing = tag_sha(remote, tag)
    if existing is not None:
        if existing != sha:
            raise PlatformBranchError(
                f"tag {tag} already exists at {existing[:12]}, not {sha[:12]}"
            )
        return tag
    _push_ref(remote, sha, "refs/tags/" + tag)
    if tag_sha(remote, tag) != sha:
        raise PlatformBranchError(f"tag {tag} did not land at {sha[:12]}")
    return tag


def _unique_archive_tag(remote: str, platform_id: str, day: Optional[date], sha: str) -> str:
    """``archive/<id>/<day>``; a second archive the same day gets ``.2``, ``.3``..."""
    base = archive_tag(platform_id, day)
    candidate, n = base, 1
    while True:
        existing = tag_sha(remote, candidate)
        if existing is None or existing == sha:
            return candidate
        n += 1
        candidate = f"{base}.{n}"


@dataclass(frozen=True)
class Archived:
    platform_id: str
    branch: str
    tag: str
    sha: str
    deleted: bool


def tag_head_for_archive(
    remote: str, platform_id: str, *, day: Optional[date] = None
) -> Optional[Archived]:
    """Tag the branch head ``archive/<id>/<day>``; the branch is kept.

    What Start over does first. None when the platform has no branch yet --
    there is nothing to keep."""
    branch = branch_of_record(platform_id)
    sha = branch_head(remote, platform_id)
    if sha is None:
        return None
    tag = create_tag(remote, sha, _unique_archive_tag(remote, platform_id, day, sha))
    return Archived(platform_id=platform_id, branch=branch, tag=tag, sha=sha, deleted=False)


def archive_branch(
    remote: str, platform_id: str, *, day: Optional[date] = None
) -> Optional[Archived]:
    """Tag the head, verify the tag, THEN delete the branch.

    The delete runs only after the tag is read back at the branch's exact sha:
    a head is never deleted without a tag that keeps its full tree."""
    kept = tag_head_for_archive(remote, platform_id, day=day)
    if kept is None:
        return None
    if tag_sha(remote, kept.tag) != kept.sha:
        raise PlatformBranchError(f"refusing to delete {kept.branch}: tag {kept.tag} does not hold its head")
    with tempfile.TemporaryDirectory(prefix="cerebrum-ref-") as tmp:
        work = Path(tmp)
        _git(["init", "-q"], work)
        deleted = _git(
            ["push", "-q", remote, f"--force-with-lease=refs/heads/{kept.branch}:{kept.sha}",
             f":refs/heads/{kept.branch}"],
            work,
        )
    if deleted.returncode != 0:
        raise PlatformBranchError(f"delete {kept.branch} failed: {(deleted.stderr or '')[-300:]}")
    return Archived(platform_id=platform_id, branch=kept.branch, tag=kept.tag, sha=kept.sha, deleted=True)


def latest_archive_tag(remote: str, platform_id: str) -> Optional[tuple]:
    """``(tag, sha)`` of the newest archive tag, or None."""
    prefix = "refs/tags/" + archive_tag_prefix(platform_id)
    rows = {}
    for sha, name in _ls_remote(remote, prefix + "*"):
        tag = name[len("refs/tags/"):]
        if tag.endswith("^{}"):
            rows[tag[:-3]] = sha
        else:
            rows.setdefault(tag, sha)
    if not rows:
        return None

    def order(tag: str):
        stamp, _, n = tag.rsplit("/", 1)[-1].partition(".")
        return (stamp, int(n) if n.isdigit() else 1)

    newest = max(rows, key=order)
    return newest, rows[newest]


def restore_branch(remote: str, platform_id: str) -> Optional[str]:
    """Recreate ``build/<id>`` from its newest archive tag when the branch is
    gone. Returns the restored sha, or None when the branch exists already or
    nothing was ever archived."""
    if branch_head(remote, platform_id) is not None:
        return None
    newest = latest_archive_tag(remote, platform_id)
    if newest is None:
        return None
    _tag, sha = newest
    _push_ref(remote, sha, "refs/heads/" + branch_of_record(platform_id))
    if branch_head(remote, platform_id) != sha:
        raise PlatformBranchError(f"restore of {branch_of_record(platform_id)} did not land at {sha[:12]}")
    return sha


def tag_release(remote: str, platform_id: str, version: str) -> Optional[tuple]:
    """Tag the certified head ``release/<id>/<version>``. ``(tag, sha)``, or
    None when the platform has no branch."""
    sha = branch_head(remote, platform_id)
    if sha is None:
        return None
    tag = create_tag(remote, sha, release_tag(platform_id, version))
    return tag, sha


def clone_branch(remote: str, platform_id: str, dest: Path) -> str:
    """Check out the branch of record into ``dest``. Returns its head sha."""
    branch = branch_of_record(platform_id)
    dest.mkdir(parents=True, exist_ok=True)
    # Byte-exact: the tree as committed, never re-encoded by the host's
    # line-ending setting.
    proc = _git(
        ["-c", "core.autocrlf=false", "clone", "-q", "--depth=1", "--branch", branch, remote, "."],
        dest,
    )
    if proc.returncode != 0:
        raise PlatformBranchError(f"clone {branch} failed: {(proc.stderr or '')[-300:]}")
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return branch_head(remote, platform_id) or ""


def push_to_branch_of_record(
    workspace: Path,
    platform_id: str,
    message: str,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Commit ``workspace`` onto ``build/<platform_id>``; return the new head.

    The ONE place a phase reaches its platform's branch: an existing branch is
    checkpointed (phase-forward, nothing rewritten); an archived one is first
    recreated from its newest archive tag; a platform with no branch yet gets
    it cut from cerebrum-builds ``main`` (which carries the Store gate)."""
    from app.factory.build.branch_attach import checkpoint
    from app.factory.build.builds_push import push_workspace

    env = env if env is not None else os.environ
    remote = builds_remote(env)
    branch = branch_of_record(platform_id)
    if branch_head(remote, platform_id) is None and restore_branch(remote, platform_id) is None:
        push_workspace(Path(workspace), env=env, session_id="", branch=branch)
        return branch_head(remote, platform_id) or ""
    return checkpoint(Path(workspace), branch, message, env)


def release_certified(remote: str, platform_id: str) -> Optional[tuple]:
    """Tag a CERTIFIED head ``release/<id>/<n>``; ``(tag, sha)``.

    Idempotent: a head that already carries a release tag keeps it; a new
    certified head gets the next number. Only the certified export path calls
    this -- a failed build is never released or registered."""
    head = branch_head(remote, platform_id)
    if head is None:
        return None
    prefix = "refs/tags/release/" + platform_id + "/"
    numbers = []
    for sha, name in _ls_remote(remote, prefix + "*"):
        tail = name[len(prefix):]
        if tail.endswith("^{}"):
            tail = tail[:-3]
        if sha == head:
            return release_tag(platform_id, tail), head
        if tail.isdigit():
            numbers.append(int(tail))
    return tag_release(remote, platform_id, str(max(numbers, default=0) + 1))
