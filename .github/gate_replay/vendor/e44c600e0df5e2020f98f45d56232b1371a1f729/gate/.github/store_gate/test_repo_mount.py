"""The acceptance container sees the product's checkout, read-only.

Live 2026-10-07 (smoke B, build/plt_9488d97f22f04dee, run 37675692243): the
gate scored ``ci_present_and_full_suite`` and ``audit_clean`` FAIL with
".github/workflows/ci.yml missing" on a branch whose CI was green. The
product's .dockerignore leaves ``.github/`` out of its image -- its call -- and
the harness ran inside the image with nothing else to read. A check whose
subject is the repository now reads the checkout, mounted where the harness's
own REPO_ROOT_ENV says.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import repo_mount

WORKFLOW = HERE.parent / "workflows" / "store-gate.yml"


def _harness(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "acceptance.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_checkout_is_mounted_read_only_under_the_harness_s_own_env_name(tmp_path):
    harness = _harness(tmp_path, 'import os\nREPO_ROOT_ENV = "ACCEPTANCE_REPO_ROOT"\n')
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    args = repo_mount.docker_args(harness, checkout)

    assert args == [
        "-v", f"{checkout.resolve()}:{repo_mount.MOUNT_POINT}:ro",
        "-e", f"ACCEPTANCE_REPO_ROOT={repo_mount.MOUNT_POINT}",
    ]


def test_the_env_name_is_read_from_the_harness_never_assumed(tmp_path):
    harness = _harness(tmp_path, 'REPO_ROOT_ENV = "SOME_OTHER_NAME"\n')
    assert repo_mount.docker_args(harness, tmp_path)[-1] == f"SOME_OTHER_NAME={repo_mount.MOUNT_POINT}"


def test_an_older_harness_still_gets_the_checkout_mounted_without_an_env(tmp_path):
    # The checkout is ALWAYS mounted: the harness itself may live only there
    # (a minimal image need not carry Factory test files). A harness that
    # declares no REPO_ROOT_ENV is told nothing -- it reads as it always did.
    harness = _harness(tmp_path, "CHECKS = ['ci_present_and_full_suite']\n")
    mount = ["-v", f"{tmp_path.resolve()}:{repo_mount.MOUNT_POINT}:ro"]
    assert repo_mount.docker_args(harness, tmp_path) == mount
    assert repo_mount.docker_args(tmp_path / "missing.py", tmp_path) == mount


# -- the harness runs from the checkout --------------------------------------
# Live 2026-10-08 (94d40722, cycle 2 smoke A, build/plt_28405ae4961d420b, run
# 37760007852): "python3: can't open file '/app/scripts/acceptance.py'". The
# writer's Dockerfile copies only app/ + alembic -- a minimal production image,
# its call -- and the gate assumed the Factory's harness was inside it.


def test_a_harness_that_declares_a_runtime_env_runs_from_the_mount(tmp_path):
    harness = _harness(tmp_path, 'RUNTIME_ROOT_ENV = "ACCEPTANCE_RUNTIME_ROOT"\n')
    assert repo_mount.exec_env(harness, "/srv/app") == ["-e", "ACCEPTANCE_RUNTIME_ROOT=/srv/app"]
    # Even when the image happens to carry a copy: the checkout's is the one
    # the Factory rendered for this commit.
    for in_image in (True, False):
        assert repo_mount.exec_path(harness, "scripts/acceptance.py", in_image) == (
            f"{repo_mount.MOUNT_POINT}/scripts/acceptance.py"
        )


def test_an_older_harness_always_runs_inside_the_image_runtime(tmp_path):
    # An older harness cannot be told where the runtime is: it judges the tree
    # it sits in (ROOT = parents[1]). Run from the mount it judged the
    # CHECKOUT -- live 2026-10-08 build/plt_5ac16f50c9384536 certified 22/22
    # because the checkout carries vendor/ and the image does not. So it is
    # copied INTO the container one level under the working directory, where
    # parents[1] is the image's runtime tree.
    harness = _harness(tmp_path, "CHECKS = []\n")
    assert repo_mount.exec_env(harness, "/app") == []
    assert repo_mount.exec_path(harness, "scripts/acceptance.py", True, "/app") == "scripts/acceptance.py"
    staged = repo_mount.exec_path(harness, "scripts/acceptance.py", False, "/app")
    assert staged == "/app/.store-gate-harness/acceptance.py"
    assert not staged.startswith(repo_mount.MOUNT_POINT)
    # No working directory known: nothing can judge the image -- fail closed
    # (the run scores "no score", Factory-owed), never the checkout.
    assert repo_mount.exec_path(harness, "scripts/acceptance.py", False, "") == "scripts/acceptance.py"


def test_no_working_directory_known_means_no_runtime_env(tmp_path):
    harness = _harness(tmp_path, 'RUNTIME_ROOT_ENV = "ACCEPTANCE_RUNTIME_ROOT"\n')
    assert repo_mount.exec_env(harness, "") == []


def test_the_exec_subcommands_print_one_line_each(tmp_path):
    # Exactly the call shapes the acceptance step uses, run from the checkout.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "acceptance.py").write_text(
        'RUNTIME_ROOT_ENV = "ACCEPTANCE_RUNTIME_ROOT"\n', encoding="utf-8"
    )

    def run(*args):
        return subprocess.run(
            [sys.executable, str(HERE / "repo_mount.py"), *args],
            capture_output=True, text=True, check=True, cwd=tmp_path,
        ).stdout.strip()

    assert run("exec-env", "scripts/acceptance.py", "/app") == "-e ACCEPTANCE_RUNTIME_ROOT=/app"
    assert run("exec-path", "scripts/acceptance.py", "0", "/app") == (
        f"{repo_mount.MOUNT_POINT}/scripts/acceptance.py"
    )
    assert run("exec-path", "scripts/acceptance.py", "1", "/app") == (
        f"{repo_mount.MOUNT_POINT}/scripts/acceptance.py"
    )


def test_the_cli_prints_one_argument_line(tmp_path):
    harness = _harness(tmp_path, 'REPO_ROOT_ENV = "ACCEPTANCE_REPO_ROOT"\n')
    out = subprocess.run(
        [sys.executable, str(HERE / "repo_mount.py"), str(harness), str(tmp_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out.endswith(f"ACCEPTANCE_REPO_ROOT={repo_mount.MOUNT_POINT}")
    assert ":ro" in out


def test_the_acceptance_container_is_started_with_the_checkout_mount():
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text.split("- name: Run scripts/acceptance.py inside the image", 1)[1].split("\n      - name:", 1)[0]
    assert "repo_mount.py scripts/acceptance.py" in step
    run = step.split("docker run -d", 1)[1].split("2>container_create.err", 1)[0]
    assert "$REPO_MOUNT_ARGS" in run
    # The harness is executed at the path the gate resolved, never assumed
    # to sit inside the image.
    assert "repo_mount.py exec-path scripts/acceptance.py" in step
    assert 'python3 "$HARNESS_PATH"' in step
    assert "python3 scripts/acceptance.py |" not in step
