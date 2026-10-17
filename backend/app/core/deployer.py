"""Package a CerebrumDev session for deploy, or prepare an edge package.

There is no cloud provider client here any more. Render is gone and that
account is suspended, so the Render API client this module used to carry was
not inert: it kept RENDER_API_KEY plumbed, kept the vendor's URL in the file,
and was one call away from being reachable again. What replaces it ships inside
the package -- deploy/contract.json and the ECS task definition derived from it.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional, Sequence, Tuple

from ..models.session import SessionState
from . import client_data

logger = logging.getLogger(__name__)

# Matches any userinfo (``user:token@`` or ``token@``) embedded in a URL, so a
# credential echoed by git in stderr/stdout is redacted before it is logged or
# returned to a caller.
_URL_CREDENTIAL_RE = re.compile(r"(https?://)[^/\s@]+@")


def _scrub_git_output(text: Optional[str]) -> str:
    """Redact embedded URL credentials from git subprocess output."""
    if not text:
        return ""
    return _URL_CREDENTIAL_RE.sub(r"\1", text)

# Dedicated deploy target repository. This must never be the CerebrumDev.ai
# repository itself. Git-push deploy is disabled when this variable is unset.
DEPLOY_REPO_URL = os.getenv("DEPLOY_REPO_URL", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Paths inside a generated package that carry client data must never be staged
# for a git push unless the target repository has passed the private-repo
# guard. Which paths those are is what the packager declared as it wrote them
# (app.core.client_data) -- never a list of names kept here.


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name).lower().strip("-")[:40]


def _run_git(args: list, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _ensure_git_identity(repo_dir: str) -> bool:
    name = _run_git(["config", "user.name"], cwd=repo_dir).stdout.strip()
    email = _run_git(["config", "user.email"], cwd=repo_dir).stdout.strip()
    if not name:
        _run_git(["config", "user.name", "CerebrumDev Deployer"], cwd=repo_dir)
    if not email:
        _run_git(["config", "user.email", "deploy@cerebrumdev.ai"], repo_dir)
    return True


def _parse_github_repo(repo_url: str) -> Tuple[str, str]:
    """Extract (owner, repo) from a GitHub HTTPS URL."""
    clean = repo_url.rstrip("/").removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    parts = clean.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Not a valid GitHub repository URL: {repo_url}")
    return parts[0], parts[1]


def _auth_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _verify_repo_private(repo_url: str, token: str) -> Tuple[bool, str]:
    """Return (ok, reason) where ok means the repo is confirmed private.

    If the repo is public, unreachable, or the API call fails, ok is False and
    reason names the repo and explains why the push was aborted.
    """
    try:
        owner, repo = _parse_github_repo(repo_url)
    except ValueError as exc:
        return False, str(exc)

    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers=_auth_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            if data.get("private") is True:
                return True, ""
            return False, f"repository {owner}/{repo} is public"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, f"repository {owner}/{repo} is unreachable or does not exist"
        try:
            body = exc.read().decode()
        except Exception:
            body = ""
        return False, f"repository {owner}/{repo} verification failed: HTTP {exc.code} - {body}"
    except Exception as exc:
        return False, f"repository {owner}/{repo} verification failed: {exc}"


def _assert_no_client_data_staged(
    repo_dir: str,
    guard_passed: bool,
    declared: Optional[Sequence[PurePosixPath]] = None,
) -> None:
    """Packaging-time scrub check.

    Raise if any client-data path is staged for commit while the private-repo
    guard has not passed. This is a fail-safe: the deployer should never stage
    these files unless the target repository has been verified private.

    ``declared`` is the packager's client-data manifest, as paths relative to
    ``repo_dir``. With no manifest nothing proves a staged file is not client
    data, so any staged file fails the check (fail closed).
    """
    if guard_passed:
        return
    status = _run_git(["status", "--short"], cwd=repo_dir)
    staged = [line for line in status.stdout.splitlines() if line and line[0] in ("A", "M", "R")]
    for line in staged:
        # status lines look like "AM path/to/file"
        path = PurePosixPath(line[3:].strip())
        if declared is None or client_data.covers(declared, path):
            raise RuntimeError(
                f"Scrub check failed: client data path '{path}' is staged "
                "but the private-repo guard has not passed."
            )


def _authenticated_repo_url(repo_url: str, token: str) -> str:
    """Return a clone/push URL with the token embedded."""
    if repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://x-access-token:{token}@", 1)
    return repo_url


def _push_package_to_branch(session_id: str, package_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Push the package files to a dedicated branch in DEPLOY_REPO_URL.

    Returns (branch_name, abort_reason). branch_name is None when the push was
    aborted; abort_reason then contains a human-readable explanation that can
    be surfaced in the deploy status.
    """
    if not DEPLOY_REPO_URL:
        logger.info(
            "DEPLOY_REPO_URL is not configured; skipping git-push deploy for %s "
            "and falling back to zip package.",
            session_id,
        )
        return None, "DEPLOY_REPO_URL not configured; using zip package"

    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN is not configured; cannot verify or push to %s", DEPLOY_REPO_URL)
        return None, "GITHUB_TOKEN not configured; cannot verify deploy target"

    is_private, reason = _verify_repo_private(DEPLOY_REPO_URL, GITHUB_TOKEN)
    if not is_private:
        logger.error("Deploy target verification failed: %s", reason)
        return None, f"Deploy target verification failed: {reason}"

    branch = f"deploy-{_safe_name(session_id)}"
    deploy_path = Path("deployments") / session_id

    with tempfile.TemporaryDirectory(prefix="cerebrum-deploy-") as clone_dir:
        clone = _run_git(["clone", "--depth", "1", _authenticated_repo_url(DEPLOY_REPO_URL, GITHUB_TOKEN), clone_dir])
        if clone.returncode != 0:
            clone_err = _scrub_git_output(clone.stderr)
            logger.error("Failed to clone deploy repo %s: %s", DEPLOY_REPO_URL, clone_err)
            return None, f"Failed to clone deploy repo: {clone_err}"

        _ensure_git_identity(clone_dir)
        _run_git(["checkout", "-B", branch], cwd=clone_dir)

        # Clean any previous package for this session
        full_deploy_path = Path(clone_dir) / deploy_path
        if full_deploy_path.exists():
            shutil.rmtree(full_deploy_path)
        full_deploy_path.mkdir(parents=True, exist_ok=True)

        # Copy package files into the repo subdir
        for item in Path(package_dir).iterdir():
            dest = full_deploy_path / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        _run_git(["add", str(deploy_path)], cwd=clone_dir)

        # Fail-safe: the guard passed above, so this assertion must succeed.
        # It is kept as defense-in-depth in case future refactors stage files
        # before verification.
        package_declared = client_data.declared(package_dir)
        _assert_no_client_data_staged(
            clone_dir,
            guard_passed=True,
            declared=(
                None
                if package_declared is None
                else [PurePosixPath(deploy_path.as_posix()) / p for p in package_declared]
            ),
        )

        status = _run_git(["status", "--short"], cwd=clone_dir)
        if not status.stdout.strip():
            logger.info("No package changes to push for %s", session_id)
            return branch, None

        commit = _run_git(
            ["commit", "-m", f"deploy({session_id}): generated deployment package"],
            cwd=clone_dir,
        )
        if commit.returncode != 0:
            commit_err = _scrub_git_output(commit.stderr)
            logger.error("Git commit failed: %s", commit_err)
            return None, f"Git commit failed: {commit_err}"

        push = _run_git(
            ["push", "-u", _authenticated_repo_url(DEPLOY_REPO_URL, GITHUB_TOKEN), branch],
            cwd=clone_dir,
        )
        if push.returncode != 0:
            push_err = _scrub_git_output(push.stderr)
            logger.error("Git push failed: %s", push_err)
            return None, f"Git push failed: {push_err}"

        logger.info("Pushed deployment package to branch %s in %s", branch, DEPLOY_REPO_URL)
        return branch, None


def deploy_to_render(
    session_id: str,
    state: SessionState,
    package_dir: str,
    service_name: str,
    env_vars: Dict[str, str],
) -> Dict[str, Any]:
    """Return the package; do not deploy to Render.

    Kept as the cloud target's entry point, but it no longer calls the vendor.
    The company left Render and that account is suspended: an API call there
    either fails or creates a service that answers nothing while this function
    reports a live URL for it. Reporting a deploy that did not happen is worse
    than refusing one.

    What replaces it ships inside the package. `deploy/contract.json` states
    the platform's port, health path, required volume and environment, and
    `deploy/aws/task-definition.json` is an ECS Fargate definition derived from
    it. Both are generated from one source, so neither can drift from the
    container they describe.

    The signature and the "packaged" status are unchanged, because every caller
    and the UI already handle that outcome — this is the path they take whenever
    credentials are absent.
    """
    return {
        "status": "packaged",
        "message": (
            "Cloud deploy is not wired to a provider. The package carries its "
            "own deploy artifacts: deploy/contract.json states what the "
            "platform needs, and deploy/aws/task-definition.json is an ECS "
            "Fargate definition derived from it. Download the package and "
            "apply them."
        ),
    }


def poll_deploy_status(service_id: str) -> Dict[str, Any]:
    """Report that no provider is wired, without calling one.

    This was the half that got left behind when the cloud deploy path was cut.
    ``deploy_to_render`` was made to refuse the vendor, but this function kept
    calling ``api.render.com`` -- and unlike that one, this is reachable from a
    route: ``routers/deploy.py`` polls it for any session whose deployment
    record carries a ``service_id``, which every session created before the cut
    still does.

    It also swallowed the failure into ``status: "unknown"``, so the endpoint
    never went red. It answered "unknown" indefinitely while a UI waited on a
    deploy that no provider was running -- the same class of lie as reporting a
    live URL for a service that answers nothing, just quieter.

    ``no_provider`` is a distinct status precisely so a caller can tell "there
    is nothing to poll" from "the poll broke". The ``service_id`` is echoed back
    because it is the record that needs clearing, not a live handle.
    """
    return {
        "status": "no_provider",
        "service_id": service_id,
        "message": (
            "No deploy provider is wired, so there is no deploy to poll. This "
            "session's service_id refers to a provider the company left. The "
            "package carries its own deploy artifacts: deploy/contract.json "
            "states what the platform needs, and deploy/aws/task-definition.json "
            "is an ECS Fargate definition derived from it."
        ),
    }


def generate_edge_package(state: SessionState, package_dir: str) -> str:
    """Create a self-contained edge package (zip) and return its path."""
    edge_dir = Path(package_dir).parent / "edge"
    if edge_dir.exists():
        shutil.rmtree(edge_dir)
    shutil.copytree(package_dir, edge_dir)

    # Add an edge-specific README
    readme = edge_dir / "EDGE_README.md"
    readme.write_text(
        f"""# Edge Package — {state.config.domain}

This is a self-contained deployment package for edge/on-premise installation.

## Run locally

```bash
docker build -t cerebrumdev-{state.session_id} .
docker run -p 8000:8000 cerebrumdev-{state.session_id}
```

## Endpoints

- `GET /health`
- `GET /v1/chain`
- `POST /v1/execute`
- `POST /v1/chat`

Set `CEREBRUM_LLM_API_KEY` to your Kimi API key for AI chat.
""",
        encoding="utf-8",
    )

    zip_path = edge_dir.parent / "edge.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in edge_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(edge_dir))
    return str(zip_path)
