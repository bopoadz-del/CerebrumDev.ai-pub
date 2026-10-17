"""Tests for the deployer's private-repository guard and zip fallback."""

from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import deployer
from app.core.deployer import (
    _assert_no_client_data_staged,
    _push_package_to_branch,
    _verify_repo_private,
    deploy_to_render,
)


class _FakeUrlopenResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_fake_urlopen(payload: dict, status: int = 200):
    def _fake_urlopen(req, timeout=None):
        return _FakeUrlopenResponse(payload, status)

    return _fake_urlopen


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def deploy_env(monkeypatch, tmp_path: Path):
    """Provide a configured deploy environment pointing at a temp package."""
    monkeypatch.setattr(deployer, "DEPLOY_REPO_URL", "https://github.com/owner/deploy-target")
    monkeypatch.setattr(deployer, "GITHUB_TOKEN", "fake-token")
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    return package_dir


def test_verify_repo_private_accepts_private_repo():
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": True, "full_name": "owner/deploy-target"}),
    ):
        ok, reason = _verify_repo_private("https://github.com/owner/deploy-target", "token")
    assert ok is True
    assert reason == ""


def test_verify_repo_private_rejects_public_repo():
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": False, "full_name": "owner/deploy-target"}),
    ):
        ok, reason = _verify_repo_private("https://github.com/owner/deploy-target", "token")
    assert ok is False
    assert "public" in reason.lower()
    assert "owner/deploy-target" in reason


def test_verify_repo_private_rejects_unreachable_repo():
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/owner/missing",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=None,
    )
    with patch("app.core.deployer.urllib.request.urlopen", side_effect=error):
        ok, reason = _verify_repo_private("https://github.com/owner/missing", "token")
    assert ok is False
    assert "unreachable" in reason.lower()


def test_push_package_to_branch_falls_back_when_deploy_repo_url_unset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(deployer, "DEPLOY_REPO_URL", "")
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    branch, reason = _push_package_to_branch("sess-unset", str(package_dir))

    assert branch is None
    assert reason is not None
    assert "DEPLOY_REPO_URL" in reason


def test_push_package_to_branch_aborts_public_repo(deploy_env):
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": False, "full_name": "owner/deploy-target"}),
    ):
        branch, reason = _push_package_to_branch("sess-public", str(deploy_env))

    assert branch is None
    assert "public" in reason.lower()


def test_push_package_to_branch_proceeds_for_private_repo(deploy_env, tmp_path: Path):
    # Simulate a successful clone/commit/push cycle without touching GitHub.
    def _fake_run_git(args, cwd=""):
        if args[0] == "clone":
            # Create the clone directory and a fake deployments tree.
            clone_dir = args[-1]
            Path(clone_dir).mkdir(parents=True, exist_ok=True)
            (Path(clone_dir) / "deployments").mkdir(exist_ok=True)
            return _completed()
        if args[0] == "status" and "--short" in args:
            return _completed("A  deployments/sess-private/Dockerfile\n")
        return _completed()

    with patch(
        "app.core.deployer.urllib.request.urlopen",
        _make_fake_urlopen({"private": True, "full_name": "owner/deploy-target"}),
    ), patch("app.core.deployer._run_git", side_effect=_fake_run_git):
        branch, reason = _push_package_to_branch("sess-private", str(deploy_env))

    assert branch == "deploy-sess-private"
    assert reason is None


def test_the_cloud_target_pushes_the_package_nowhere(deploy_env, monkeypatch):
    """Was test_deploy_to_render_surfaces_public_repo_abort.

    That test guarded one way the Render path could leak a package: pushing it
    to a repository that turned out to be public. The path is gone -- the
    company left Render -- so the guard is replaced by the stronger property
    the new behaviour gives for free: the cloud target contacts nothing at all,
    so there is no repository to get wrong and no package to leak.

    The two RENDER_* credentials this test used to seed are gone from the module
    entirely, so there is nothing left to seed -- which is the point.
    """

    class FakeState:
        config = type("Config", (), {"domain": "medical"})()

    opened = []
    with patch(
        "app.core.deployer.urllib.request.urlopen",
        lambda *a, **k: opened.append(a) or _make_fake_urlopen({})(*a, **k),
    ):
        result = deploy_to_render(
            "sess-abort",
            FakeState(),
            str(deploy_env),
            "cerebrumdev-medical-sess",
            {"CEREBRUM_MASTER_KEY": "key"},
        )

    assert not opened, "the cloud target opened a network connection"
    assert result["status"] == "packaged"
    assert "deploy/contract.json" in result["message"]


def test_assert_no_client_data_staged_raises_when_guard_not_passed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run = lambda args, cwd: subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    _run(["init"], cwd=str(repo))
    _run(["config", "user.email", "test@example.com"], cwd=str(repo))
    _run(["config", "user.name", "Test"], cwd=str(repo))

    docs = repo / "data" / "docs"
    docs.mkdir(parents=True)
    (docs / "file.txt").write_text("secret", encoding="utf-8")
    _run(["add", "."], cwd=str(repo))

    with pytest.raises(RuntimeError, match="Scrub check failed"):
        _assert_no_client_data_staged(str(repo), guard_passed=False)


def test_assert_no_client_data_staged_passes_when_guard_passed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run = lambda args, cwd: subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    _run(["init"], cwd=str(repo))
    _run(["config", "user.email", "test@example.com"], cwd=str(repo))
    _run(["config", "user.name", "Test"], cwd=str(repo))

    docs = repo / "data" / "docs"
    docs.mkdir(parents=True)
    (docs / "file.txt").write_text("secret", encoding="utf-8")
    _run(["add", "."], cwd=str(repo))

    # When the guard has passed, client data may legitimately be staged.
    _assert_no_client_data_staged(str(repo), guard_passed=True)


def test_the_scrub_check_reads_what_the_packager_declared(tmp_path: Path):
    """Client data is what the packager declared as it wrote it -- a manifest
    in the package -- not a list of names the deployer keeps."""
    from app.core import client_data

    package = tmp_path / "package"
    (package / "uploads").mkdir(parents=True)
    (package / "uploads" / "contract.pdf").write_text("secret", encoding="utf-8")
    (package / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    client_data.declare(package, package / "uploads")
    declared = client_data.declared(package)
    assert [str(p) for p in declared] == ["uploads"]

    repo = tmp_path / "repo"
    repo.mkdir()
    _run = lambda args, cwd: subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    _run(["init"], cwd=str(repo))
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    _run(["add", "Dockerfile"], cwd=str(repo))
    # A file the packager did not declare is not client data.
    _assert_no_client_data_staged(str(repo), guard_passed=False, declared=declared)

    (repo / "uploads").mkdir()
    (repo / "uploads" / "contract.pdf").write_text("secret", encoding="utf-8")
    _run(["add", "uploads"], cwd=str(repo))
    with pytest.raises(RuntimeError, match="Scrub check failed"):
        _assert_no_client_data_staged(str(repo), guard_passed=False, declared=declared)


def test_verify_repo_private_invalid_url():
    ok, reason = _verify_repo_private("https://example.com/not-github", "token")
    assert ok is False
    assert "Not a valid GitHub repository URL" in reason


def test_the_cloud_target_does_not_call_render(deploy_env, monkeypatch):
    """Render is gone; the company left it and the account is suspended.

    Calling that API now either fails or, worse, creates a service on a dead
    account and reports a live URL that never answers. The delivered platform
    carries its own deploy artifacts instead, so the cloud target must say so
    and must not reach for the vendor.

    This used to patch a ``_render_request`` helper and assert it went unused.
    That helper has since been deleted along with the rest of the client, so the
    assertion is now made against the network itself -- which is the property
    that actually matters and cannot be satisfied by renaming a function.
    """
    from app.core import deployer

    def _explode(*args, **kwargs):
        raise AssertionError("the cloud target made a network call")

    monkeypatch.setattr(deployer.urllib.request, "urlopen", _explode)

    result = deployer.deploy_to_render(
        "sess-1", None, "/tmp/pkg", "svc", {},
    )

    assert result["status"] == "packaged"
    message = result["message"].lower()
    assert "deploy/contract.json" in message, (
        "the refusal must name the artifact that replaces it, or the caller is "
        "told no with nowhere to go"
    )


def test_poll_deploy_status_does_not_reach_the_vendor_either(monkeypatch):
    """The status poll was left pointing at Render when the deploy path was cut.

    ``deploy_to_render`` was made to refuse the vendor, but
    ``poll_deploy_status`` still called ``api.render.com`` -- and it is the half
    that is reachable from a route. ``routers/deploy.py`` polls it whenever a
    persisted session carries a ``service_id``, which every session created
    before the cut still does.

    Worse, it swallows the exception and returns ``status: unknown``, so the
    endpoint never fails loudly. It just answers "unknown" forever while a UI
    waits for a deploy that no provider is running.
    """
    from app.core import deployer

    def _explode(*args, **kwargs):
        raise AssertionError("poll_deploy_status made a network call")

    monkeypatch.setattr(deployer.urllib.request, "urlopen", _explode)

    result = deployer.poll_deploy_status("srv-abc123")

    assert result["status"] != "unknown", (
        "'unknown' is what the old vendor call degraded to on failure; the "
        "answer must distinguish 'no provider is wired' from 'the poll broke'"
    )
    message = result["message"].lower()
    assert "provider" in message or "not wired" in message, (
        f"the status must say why there is nothing to poll, got: {result['message']!r}"
    )


def test_the_render_api_client_is_gone_from_the_source():
    """No code path may retain a client for a provider the company left.

    A dead client is not inert: it keeps the credentials plumbed, keeps the
    vendor's URL in the file, and is one call away from being reachable again.

    This reads the AST rather than grepping the text, because the docstrings in
    this module and in deployer.py have to be free to *explain* that the vendor
    is gone. A plain substring scan fails on its own explanation, which would
    make the honest comment the thing that breaks the build.
    """
    import ast
    from pathlib import Path

    import app.core.deployer as deployer_module

    tree = ast.parse(Path(deployer_module.__file__).read_text(encoding="utf-8"))

    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstrings
    ]
    offenders = [text for text in literals if "api.render.com" in text]
    assert not offenders, f"the Render API URL is still a live string: {offenders}"

    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for banned in ("RENDER_API_KEY", "RENDER_OWNER_ID", "_render_request",
                   "_build_service_payload", "_deploy_to_render_unused"):
        assert banned not in names, f"{banned} is still defined or used in deployer.py"
