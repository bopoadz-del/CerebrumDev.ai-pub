"""Git subprocess output must never leak the embedded access token."""

from __future__ import annotations

import logging
import os
import subprocess


from app.core import deployer

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_PW_1 = "test-" + __import__("secrets").token_hex(8)

TOKEN = "test-token-" + __import__("secrets").token_hex(10)


def test_scrub_removes_embedded_token():
    dirty = (
        "fatal: unable to access "
        "'https://x-access-token:" + _GENERATED_PW_1 + "@github.com/acme/deploy.git/': "
        "The requested URL returned error: 403"
    )
    clean = deployer._scrub_git_output(dirty)
    assert TOKEN not in clean
    assert "x-access-token" not in clean
    assert "github.com/acme/deploy.git" in clean


def test_scrub_handles_none_and_empty():
    assert deployer._scrub_git_output(None) == ""
    assert deployer._scrub_git_output("") == ""


def test_push_failure_reason_is_scrubbed(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(deployer, "DEPLOY_REPO_URL", "https://github.com/acme/deploy.git")
    monkeypatch.setattr(deployer, "GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(deployer, "_verify_repo_private", lambda url, tok: (True, "private"))

    def fake_git(args, cwd=None):
        if args[:1] == ["clone"]:
            os.makedirs(args[-1], exist_ok=True)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:1] == ["status"]:
            return subprocess.CompletedProcess(args, 0, stdout="M file\n", stderr="")
        if args[:1] == ["push"]:
            return subprocess.CompletedProcess(
                args, 1, stdout="",
                stderr=("remote: Permission denied to "
                        "https://x-access-token:" + _GENERATED_PW_1 + "@github.com/acme/deploy.git"),
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(deployer, "_run_git", fake_git)

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "app.py").write_text("print('hi')\n", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        branch, reason = deployer._push_package_to_branch("sess1", str(pkg))

    assert branch is None
    assert reason is not None
    assert TOKEN not in reason, "abort reason leaked the token"
    assert TOKEN not in caplog.text, "log record leaked the token"
