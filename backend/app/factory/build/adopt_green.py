"""Adopt a build branch that already passed the Store gate.

Audit of cerebrum-builds, 2026-09-19: several sessions had a ``build/<session>-*``
branch that was green in Docker while the Factory called the session failed
or stalled -- the verdict had been collected for nobody. Resuming such a
session used to mean rebuilding from scratch a platform that had already
passed.

The trap: the customer's zip is cut from the LOCAL workspace, while the gate
verified the BRANCH's tree. A later failed run may have rewritten the
workspace. Stamping "passed" on files the gate never saw would be a lie, so a
branch is adopted only when every product file it carries is byte-identical
(git blob sha) to the workspace. Otherwise nothing is adopted and the caller
falls through to the normal resume.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote
from urllib.request import urlopen

logger = logging.getLogger(__name__)

#: Paths the push adds or rewrites on the branch that the workspace never has
#: (the Store gate's own workflow), so they cannot be part of the comparison.
_BRANCH_ONLY_PREFIXES = (".github/",)


def git_blob_sha(data: bytes) -> str:
    """The sha git records for a file's content."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 -- git's id, not security


def branch_tree(
    owner: str, repo: str, sha: str, *, token: str, opener: Callable[..., Any] = urlopen
) -> Optional[Dict[str, str]]:
    """``{path: blob_sha}`` for the commit, or None when it cannot be read whole."""
    from app.factory.build.builds_push import github_request

    path = f"/repos/{owner}/{repo}/git/trees/{quote(sha, safe='')}?recursive=1"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status >= 400 or not isinstance(body, Mapping) or body.get("truncated"):
        return None
    return {
        str(item.get("path")): str(item.get("sha"))
        for item in body.get("tree") or []
        if isinstance(item, Mapping) and item.get("type") == "blob"
    }


def workspace_matches_tree(workspace: Path, tree: Mapping[str, str]) -> Tuple[bool, List[str]]:
    """Every product file on the branch is byte-identical in the workspace."""
    from app.factory.build.builds_push import FACTORY_INTERNAL_NAMES

    differing: List[str] = []
    compared = 0
    for rel, blob in sorted(tree.items()):
        if rel.startswith(_BRANCH_ONLY_PREFIXES):
            continue
        if any(part in FACTORY_INTERNAL_NAMES for part in rel.split("/")):
            continue
        # The ledger keeps growing after the push (the failure that followed
        # is recorded in it); it is bookkeeping, not product.
        if rel in ("build_ledger.jsonl", "receipt.json"):
            continue
        local = workspace / rel
        if not local.is_file() or git_blob_sha(local.read_bytes()) != blob:
            differing.append(rel)
            if len(differing) >= 10:
                break
        compared += 1
    return (compared > 0 and not differing), differing


def find_adoptable_branch(
    workspace: Path | str,
    session_id: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
) -> Optional[Dict[str, Any]]:
    """Newest ``build/<session>-*`` branch that passed the gate AND equals the
    workspace. None when there is none, or when anything cannot be verified."""
    from app.factory.build.builds_push import (
        build_refs_of_record,
        builds_token,
        parse_builds_repo,
    )
    from app.factory.build.platform_identity import recorded_platform_id
    from app.factory.build.n3_store_gate import BuildsTarget, fetch_store_gate_status

    blob = env if env is not None else os.environ
    token = builds_token(blob)
    pid = recorded_platform_id(workspace)
    if not token or not (session_id or pid):
        return None
    owner, repo, _url = parse_builds_repo(blob)
    root = Path(workspace)
    try:
        refs = build_refs_of_record(
            owner, repo, platform_id=pid, session_id=session_id, token=token, opener=opener
        )
    except Exception:  # noqa: BLE001 -- GitHub down means "cannot adopt", not an error
        logger.warning("adopt_green: cannot list branches for %s", session_id, exc_info=True)
        return None
    for branch, sha in reversed(refs):
        try:
            snap = fetch_store_gate_status(
                BuildsTarget(owner=owner, repo=repo, sha=sha, branch=branch, session_id=session_id),
                env=blob,
                opener=opener,
            )
            if not (snap.ok and snap.total and snap.passed == snap.total):
                continue
            tree = branch_tree(owner, repo, sha, token=token, opener=opener)
            if tree is None:
                continue
            same, differing = workspace_matches_tree(root, tree)
            if not same:
                logger.info(
                    "adopt_green: %s passed the gate but the workspace differs (%s)",
                    branch, ", ".join(differing[:5]) or "no comparable files",
                )
                continue
            return {"branch": branch, "sha": sha, "owner": owner, "repo": repo,
                    "score": f"{snap.passed}/{snap.total}"}
        except Exception:  # noqa: BLE001
            logger.warning("adopt_green: could not verify %s", branch, exc_info=True)
    return None


def cli_authored_ids(workspace: Path | str) -> Sequence[str]:
    from app.factory.build.authorship import agent_written_handler_ids_in_workspace

    return agent_written_handler_ids_in_workspace(Path(workspace))
