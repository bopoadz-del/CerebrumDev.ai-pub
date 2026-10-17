"""Deliver a green build as a GitHub repository.

The client chooses the delivery format at request time (blueprint
``delivery_format``): ``zip`` (the Floor export) or ``github_repo``. This
module pushes the workspace to a private repo under the factory's GitHub
token and returns the URL for the build status / Floor to show.

Fail-closed: any failure raises, and the runner turns a chosen-but-failed
delivery into a named build failure (``github_delivery_failed``) — a client
who asked for a repo must not be handed a silent zip instead.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Final, Optional

_TOKEN_ENV: Final = "CEREBRUM_BUILDS_GITHUB_TOKEN"
_OWNER_ENV = "CEREBRUM_BUILDS_GITHUB_OWNER"


def _token() -> str:
    token = (
        os.getenv(_TOKEN_ENV, "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )
    if not token:
        raise RuntimeError(
            "github_delivery_failed: no GitHub token "
            f"({_TOKEN_ENV} unset) — cannot push the repo"
        )
    return token


def _api_user(token: str) -> str:
    """The login the token acts as, for the clone URL."""
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cerebrumdev-factory",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return str(json.loads(resp.read().decode("utf-8"))["login"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"github_delivery_failed: could not resolve the token's user: {exc}"
        ) from exc


def _repo_name(product_id: str, session_id: str) -> str:
    base = f"{product_id}-{session_id[:8] or 'build'}"
    return re.sub(r"[^a-zA-Z0-9._-]", "-", base)[:80].strip("-") or "platform"


def _create_repo(token: str, owner: Optional[str], name: str) -> str:
    """Create a private repo; returns the login it was created under."""
    login = owner or _api_user(token)
    endpoint = (
        f"https://api.github.com/orgs/{login}/repos"
        if owner
        else "https://api.github.com/user/repos"
    )
    body = json.dumps({"name": name, "private": True, "auto_init": False}).encode()
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cerebrumdev-factory",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data["html_url"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"github_delivery_failed: could not create repo {name!r}: {exc}"
        ) from exc


def push_workspace_repo(
    workspace: Path | str,
    *,
    product_id: str,
    session_id: str = "",
) -> str:
    """Push *workspace* to a new private GitHub repo. Returns the html URL."""
    root = Path(workspace).resolve()
    token = _token()
    owner = os.getenv(_OWNER_ENV, "").strip() or None
    name = _repo_name(product_id, session_id)
    url = _create_repo(token, owner, name)
    login = owner or _api_user(token)
    remote = f"https://x-access-token:{token}@github.com/{login}/{name}.git"

    def _git(*args: str) -> None:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"github_delivery_failed: git {' '.join(args[:2])} failed: "
                f"{(proc.stderr or proc.stdout or '')[:300]}"
            )

    _git("init", "-q")
    _git("add", "-A")
    try:
        _git("-c", "user.email=factory@cerebrum-dev.com",
             "-c", "user.name=CerebrumDev Factory", "commit", "-q",
             "-m", f"Generated platform: {product_id}")
    except RuntimeError:
        # Empty tree / nothing to commit is not a delivery failure shape we
        # can satisfy; let it raise and be named.
        raise
    _git("branch", "-M", "main")
    _git("remote", "add", "origin", remote)
    _git("push", "-q", "-u", "origin", "main")
    return url
