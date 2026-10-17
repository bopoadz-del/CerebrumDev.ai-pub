"""Resume a build from its cerebrum-builds branch, pasted into the Floor.

The branch IS the build: every run pushes its tree to
``build/sess_<id>-<suffix>`` on cerebrum-builds. Pasting that link (or the
bare ``sess_<id>``) re-attaches the tree as the workspace and continues at the
first phase not yet passed -- never re-running COLLECTOR, CLONER or WRITER
while the branch proves them done.

Phase state comes from the branch itself. The build ledger is Factory-internal
and never ships, so a clean checkout carries none; the tree is the evidence:
handlers in ``app/actions/``, a suite in ``tests/`` and the compiled
``docs/blueprint`` together mean COLLECTOR, CLONER and WRITER finished.

The local attach workspace is keyed by the branch's head sha. Pasting the same
link again while the head has not moved re-enters that workspace and its
ledger, so a run killed mid-TESTER resumes at TESTER -- not from the branch
evidence again, and never earlier.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from app.factory.build.builds_push import (
    BRANCH_PREFIX,
    STORE_GATE_PATH,  # noqa: F401 -- re-exported: the gate path has one definition
    STORE_GATE_PATHS,
    BuildsPushError,
    builds_token,
    github_request,
    build_refs_of_record,
    is_build_branch,
    parse_builds_repo,
    session_token,
)

NOT_A_BUILD_LINK = "Not a cerebrum-builds session link"
DERIVED_DONE = ("COLLECTOR", "CLONER", "WRITER")

#: A session token anywhere in the message, however wrapped, means THAT
#: session: attach it or refuse it by name. It must never read as ordinary
#: chat, which can start a fresh generation (live 2026-09-29: a phone added
#: a period after "build/sess_...-2a1daefe" and a full build re-ran over a
#: tree whose gate was already green). Tokens are read by shape
#: (builds_push.session_token), links by their URL structure.


def _tree_link(message: str, env: Mapping[str, str]) -> Optional[Tuple[str, str, str]]:
    """``(owner, repo, branch)`` for a link on the builds repo's host shaped
    ``/<owner>/<repo>/<view>/<branch...>``; ``None`` when no token is one."""
    from urllib.parse import urlsplit

    host = urlsplit(parse_builds_repo(env)[2]).hostname
    for word in message.split():
        try:
            parts = urlsplit(word.strip("<>()[]{}'\""))
        except ValueError:
            continue
        if not parts.scheme or parts.hostname != host:
            continue
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) < 4:
            continue
        branch = "/".join(segments[3:]).rstrip("/.,)")
        return segments[0], segments[1], branch
    return None


class AttachError(RuntimeError):
    """The link resolved to nothing that can be attached."""


def parse_build_link(text: str, env: Mapping[str, str] | None = None) -> Tuple[Optional[str], Optional[str]]:
    """``(branch, None)``, ``(None, refusal)``, or ``(None, None)`` for "not a link".

    A GitHub tree link to any repo other than the configured cerebrum-builds,
    or one that names no ``build/sess_*`` branch, is refused -- never guessed.
    """
    env = env if env is not None else os.environ
    message = str(text or "").strip()
    link = _tree_link(message, env)
    if link:
        owner, repo, branch = link
        want_owner, want_repo, _ = parse_builds_repo(env)
        if (owner.lower(), repo.lower().removesuffix(".git")) != (want_owner.lower(), want_repo.lower()):
            return None, NOT_A_BUILD_LINK
        if not is_build_branch(branch):
            return None, NOT_A_BUILD_LINK
        return branch, None
    from app.factory.build.platform_identity import branch_of_record, is_platform_id

    for word in message.split():
        bare = word.strip("\"'`()[]{}<>.,;:!?").lower()
        if bare.startswith(BRANCH_PREFIX):
            bare = bare[len(BRANCH_PREFIX):]
        if is_platform_id(bare):
            return branch_of_record(bare), None
        found = session_token(word)
        if found:
            session, suffix = found
            if suffix:
                return f"build/{session}{suffix}", None
            return resolve_session_branch(session, env), None
    if _links_into_builds_repo(message, env):
        # A link into the builds repo that names no build branch.
        return None, NOT_A_BUILD_LINK
    return None, None


def _links_into_builds_repo(message: str, env: Mapping[str, str]) -> bool:
    """Any URL in the message whose path names the configured builds repo --
    read from the URL's structure, not by searching the message for a name."""
    from urllib.parse import urlsplit

    _owner, want_repo, _url = parse_builds_repo(env)
    want = want_repo.lower()
    for token in message.split():
        try:
            parts = urlsplit(token.strip("<>()[],."))
        except ValueError:
            continue
        if not parts.netloc:
            continue
        segments = [s.lower().removesuffix(".git") for s in parts.path.split("/") if s]
        if want in segments:
            return True
    return False


def resolve_session_branch(session: str, env: Mapping[str, str]) -> str:
    """The newest ``build/<session>-*`` branch; the only one when there is one."""
    owner, repo, _ = parse_builds_repo(env)
    token = builds_token(env)
    refs = build_refs_of_record(owner, repo, platform_id=None, session_id=session, token=token)
    if not refs:
        raise AttachError(f"no cerebrum-builds branch for {session}")
    if len(refs) == 1:
        return refs[0][0]
    dated = []
    for branch, sha in refs:
        status, body = github_request("GET", f"/repos/{owner}/{repo}/commits/{sha}", token=token)
        when = ""
        if status < 400 and isinstance(body, dict):
            when = str(((body.get("commit") or {}).get("committer") or {}).get("date") or "")
        dated.append((when, branch))
    return max(dated)[1]


@dataclass
class Attached:
    branch: str
    sha: str
    workspace: Path
    blueprint: Dict[str, Any]
    plan: Optional[Dict[str, Any]]
    reused: bool
    #: Phases the branch proves passed: the tree evidence plus every
    #: ``factory: <PHASE> passed`` checkpoint at the tip of its history.
    passed: tuple = DERIVED_DONE


#: Git trailer key a checkpoint commit carries. The phase is a typed record
#: in the commit, read back with git's own trailer parser -- never parsed
#: out of the subject line.
CHECKPOINT_TRAILER = "Factory-Checkpoint"


def checkpoint_message(phase: str) -> str:
    """The commit message for a passed-phase checkpoint: a human subject plus
    the trailer checkpointed_phases reads."""
    return "factory: %s passed\n\n%s: %s" % (phase, CHECKPOINT_TRAILER, phase)


def checkpointed_phases(workspace: Path) -> tuple:
    """Phases recorded as passed by checkpoint commits at the branch tip.

    Walks newest to oldest and stops at the first commit without the
    checkpoint trailer: any later change to the tree invalidates what it
    recorded. The phase is read with git's own trailer parser.
    """
    fmt = "%%(trailers:key=%s,valueonly,separator=%%x2C)%%x00" % CHECKPOINT_TRAILER
    proc = _git(["log", "--format=" + fmt, "-n", "50"], workspace)
    found = []
    for record in (proc.stdout or "").split(chr(0))[:-1]:
        phases = [p.strip() for p in record.strip().split(",") if p.strip()]
        if not phases:
            break
        for phase in phases:
            if phase not in found:
                found.append(phase)
    return tuple(found)


def _git(args, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env, timeout=600)


def _remote_url(env: Mapping[str, str]) -> str:
    owner, repo, url = parse_builds_repo(env)
    token = builds_token(env)
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git" if token else url + ".git"


def head_sha(branch: str, env: Mapping[str, str]) -> str:
    proc = _git(["ls-remote", _remote_url(env), f"refs/heads/{branch}"], Path.cwd())
    line = (proc.stdout or "").strip().split()
    if proc.returncode != 0 or not line:
        raise AttachError(f"branch {branch} not found on cerebrum-builds")
    return line[0]


def branch_proves_writer_done(tree: Path) -> bool:
    return (
        any((tree / "app" / "actions").glob("*.py"))
        and any((tree / "tests").glob("test_*.py"))
        and (tree / "docs" / "blueprint").is_dir()
    )


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _branch_blueprint(workspace: Path) -> Optional[Dict[str, Any]]:
    """The blueprint the build was compiled from, as a validated dict.

    ``product-dna/product_blueprint.yaml`` is the canonical copy.
    ``docs/blueprint/product_blueprint.json`` carries extra build-time keys the
    model forbids, so it is only a fallback, filtered to the model's fields.
    """
    from app.factory.blueprint import ProductBlueprint, load_blueprint

    yaml_path = workspace / "product-dna" / "product_blueprint.yaml"
    if yaml_path.is_file():
        try:
            return load_blueprint(yaml_path).model_dump(mode="json")
        except Exception:  # noqa: BLE001 -- fall through to the JSON copy
            pass
    raw = _read_json(workspace / "docs" / "blueprint" / "product_blueprint.json")
    if not raw:
        return None
    known = set(ProductBlueprint.model_fields)
    try:
        return ProductBlueprint.model_validate({k: v for k, v in raw.items() if k in known}).model_dump(
            mode="json"
        )
    except Exception:  # noqa: BLE001
        return None


def attach(branch: str, parent: Path, env: Mapping[str, str] | None = None) -> Attached:
    """Clone (or re-enter) ``branch`` under ``parent``; return what was attached."""
    env = env if env is not None else os.environ
    if not is_build_branch(branch or ""):
        raise AttachError(NOT_A_BUILD_LINK)
    sha = head_sha(branch, env)
    slug = branch.split("/", 1)[1]
    # Absolute: git runs with the parent as cwd, so a relative path would be
    # resolved twice.
    workspace = (Path(parent) / f"{slug}@{sha[:7]}").resolve()
    reused = (workspace / ".git").is_dir() or (workspace / "build_ledger.jsonl").is_file()
    if not reused:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        proc = _git(["clone", "--quiet", "--branch", branch, _remote_url(env), str(workspace)], workspace.parent)
        if proc.returncode != 0:
            raise AttachError(f"could not clone {branch}: {(proc.stderr or '').strip()[-300:]}")
        got = (_git(["rev-parse", "HEAD"], workspace).stdout or "").strip()
        if got != sha:
            raise AttachError(f"{branch} moved during clone ({sha[:7]} -> {got[:7]})")
    blueprint = _branch_blueprint(workspace)
    if not blueprint:
        raise AttachError(f"{branch} carries no readable product blueprint")
    plan = _read_json(workspace / "product-dna" / "capability_resolution.json")
    if not branch_proves_writer_done(workspace):
        raise AttachError(
            f"{branch} does not carry a finished WRITER pass (app/actions, tests, docs/blueprint)"
        )
    passed = tuple(dict.fromkeys(DERIVED_DONE + checkpointed_phases(workspace)))
    return Attached(branch=branch, sha=sha, workspace=workspace, blueprint=blueprint, plan=plan,
                    reused=reused, passed=passed)


def carry_mains_gate(tree: Path) -> None:
    """Replace every STORE_GATE_PATHS entry in ``tree`` with ``FETCH_HEAD``'s.

    The workflow and its helpers move as ONE unit: a gate path ``main`` no
    longer has is removed, one it has is restored byte-for-byte. A gate path
    ``main`` does not carry at all is left as the branch has it (nothing to
    carry). Called after ``git fetch origin main`` in ``tree``.
    """
    for rel in STORE_GATE_PATHS:
        if _git(["cat-file", "-e", f"FETCH_HEAD:{rel}"], tree).returncode != 0:
            continue
        target = tree / rel
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        _git(["-c", "core.autocrlf=false", "checkout", "FETCH_HEAD", "--", rel], tree)


#: The commit message of a carry that changed the branch's gate.
CARRY_MESSAGE = "factory: carry main's Store gate before the gate runs"


def carry_gate_onto_branch(branch: str, env: Mapping[str, str] | None = None) -> str:
    """Make ``branch`` carry ``main``'s WHOLE Store gate; return its head sha.

    A dispatch runs the gate workflow and its helpers from the dispatched
    branch's own tree. A branch whose last checkpoint predates a gate fix runs
    the OLD gate -- live 2026-10-10 (store-gate run 38040326004) an ops
    re-dispatch on a branch cut before builds#41 ran its stale
    ``repo_mount.exec_path``, the harness judged the mounted checkout instead
    of the image, and a false 22/22 was written. So every dispatch first
    carries ``main``'s gate (``carry_mains_gate``, as every checkpoint does);
    a branch that already carries it is left untouched (no commit). Fails
    closed: a branch that cannot be cloned, a ``main`` that cannot be fetched
    or a carry that cannot be pushed raises, and nothing is dispatched.
    """
    import tempfile

    from app.factory.build.builds_push import GIT_EMAIL, GIT_NAME, push_with_retry

    env = env if env is not None else os.environ
    token = builds_token(env)
    if not token:
        raise BuildsPushError("CEREBRUM_BUILDS_GITHUB_TOKEN missing -- cannot carry main's Store gate")
    tmp = Path(tempfile.mkdtemp(prefix="cerebrum-gate-carry-"))
    try:
        proc = _git(["clone", "--quiet", "--depth=1", "--branch", branch, _remote_url(env), "."], tmp)
        if proc.returncode != 0:
            raise BuildsPushError(f"gate carry: cannot clone {branch}: {(proc.stderr or '')[-300:]}")
        fetched = _git(["fetch", "--quiet", "--depth=1", "origin", "main"], tmp)
        if fetched.returncode != 0:
            raise BuildsPushError(
                f"gate carry: cannot fetch main to carry its Store gate onto {branch}: "
                f"{(fetched.stderr or '')[-300:]}"
            )
        carry_mains_gate(tmp)
        _git(["add", "-A", "--", *STORE_GATE_PATHS], tmp)
        if _git(["diff", "--cached", "--quiet"], tmp).returncode != 0:
            _git(["-c", f"user.email={GIT_EMAIL}", "-c", f"user.name={GIT_NAME}",
                  "commit", "-q", "-m", CARRY_MESSAGE], tmp)
            push_with_retry(
                lambda args, cwd: _git(args, cwd),
                ["push", "origin", f"HEAD:{branch}"],
                cwd=tmp,
                token=token,
                label="gate carry push failed",
            )
        sha = (_git(["rev-parse", "HEAD"], tmp).stdout or "").strip()
        if not sha:
            raise BuildsPushError(f"gate carry: no head sha for {branch}")
        return sha
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def checkpoint(workspace: Path, branch: str, message: str, env: Mapping[str, str] | None = None) -> str:
    """Commit the exportable tree onto ``branch`` and push it. Returns the sha.

    Phase-forward only: the branch is fetched and the workspace is laid over
    it, so nothing on the branch is rewritten. Factory-internal files never
    ship (builds_push.is_exported).
    """
    import tempfile

    from app.factory.build.builds_push import (
        GIT_EMAIL,
        GIT_NAME,
        _sync_workspace_onto_tree,
        push_with_retry,
    )

    env = env if env is not None else os.environ
    if not builds_token(env):
        raise BuildsPushError("CEREBRUM_BUILDS_GITHUB_TOKEN missing -- cannot checkpoint")
    tmp = Path(tempfile.mkdtemp(prefix="cerebrum-checkpoint-"))
    try:
        proc = _git(["clone", "--quiet", "--depth=1", "--branch", branch, _remote_url(env), "."], tmp)
        if proc.returncode != 0:
            raise BuildsPushError(f"checkpoint clone failed: {(proc.stderr or '')[-300:]}")
        _sync_workspace_onto_tree(Path(workspace), tmp)
        # The Store gate belongs to cerebrum-builds `main`, and a branch runs
        # the copy IN ITS OWN TREE. Carry main's current gate onto every
        # checkpoint, so a branch built under an older gate is judged by
        # today's -- one gate for every build, not one per branch age.
        fetched = _git(["fetch", "--quiet", "--depth=1", "origin", "main"], tmp)
        if fetched.returncode == 0:
            carry_mains_gate(tmp)
        _git(["add", "-A"], tmp)
        _git(["-c", f"user.email={GIT_EMAIL}", "-c", f"user.name={GIT_NAME}",
              "commit", "--allow-empty", "-q", "-m", message], tmp)
        push_with_retry(
            lambda args, cwd: _git(args, cwd),
            ["push", "origin", f"HEAD:{branch}"],
            cwd=tmp,
            token=builds_token(env),
            label="checkpoint push failed",
        )
        return (_git(["rev-parse", "HEAD"], tmp).stdout or "").strip()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
