"""Every step whose failure is scored records its outcome under ``bash -e``.

Live 2026-10-07 (build/plt_ebdc6359d25b4bba, run 37673093759): the product's
Dockerfile had an invalid HEALTHCHECK and ``docker build`` failed. GitHub runs
every ``run:`` block under ``bash -e``; with ``pipefail`` the failing pipeline
exited the step BEFORE ``IMAGE_BUILT`` was written. The failed-build scorer
(gated on ``IMAGE_BUILT == '0'``) never ran, store_gate.json read
``no score`` / ``via: scripts/acceptance.py-in-docker``, and the Factory --
correctly, from what it was given -- reported "the Factory's own workflow
failed before scoring". A product defect never reached the writer.

The same class sat in every step whose outcome is a verdict: a container that
cannot be created (``docker run`` fails) ended the boot steps before
``STORE_POSTGRES_BOOT`` / ``BOOT_CRASHED`` were written, and a failed scanner
install ended the audit step -- and a failed step skips every later step that
is not ``if: always()``, the acceptance run included.

These tests run each step's own script, extracted from store-gate.yml, under
``bash -e`` with a stand-in ``docker``: whatever the product does, the step
records it as data and exits 0 so the scoring steps run.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "store-gate.yml"
BUILD = "Build artifact image"
PG_BOOT = "Boot the image on Postgres (measures postgres_boot_200)"
AUDIT = "Run bandit and export the verdict (measures audit_clean)"
ACCEPTANCE = "Run scripts/acceptance.py inside the image"
BASH = shutil.which("bash")

#: A docker that fails ``build`` and ``run`` the way a broken product does.
FAILING_DOCKER = (
    'case "$1" in\n'
    "  build) echo 'dockerfile parse error on line 28: Unknown type in HEALTHCHECK'; exit 1;;\n"
    "  run) echo 'docker: Error response from daemon: invalid mount config' >&2; exit 125;;\n"
    "  *) exit 1;;\n"
    "esac\n"
)
WORKING_DOCKER = "exit 0\n"


def _step_script(name: str) -> str:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == name:
                return step["run"].replace("${{ github.sha }}", "test")
    raise AssertionError(f"store-gate.yml has no step named {name!r}")


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, step: str, docker: str, *, extra_shims=(), env=None) -> tuple:
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    _shim(shim_dir, "docker", docker)
    for name, body in extra_shims:
        _shim(shim_dir, name, body)
    github_env = tmp_path / "github_env"
    github_env.write_text("", encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(_step_script(step), encoding="utf-8", newline="\n")
    env = dict(os.environ if env is None else env)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_ENV"] = str(github_env)
    # What the "Resolve the image's declared persistence root" step
    # exports for the boot steps.
    env.setdefault("STORAGE_MOUNT_ARGS", "-v storegate-storage-test:/data")
    env.setdefault("STORAGE_MOUNT_ARGS_PG", "-v storegate-pg-test:/data")
    # What the build step's own env sets: the account's mirror.
    env.setdefault("IMAGE_MIRROR", "ghcr.io/acme/mirror")
    # GitHub's default for a run block with no `shell:` is `bash -e {0}`.
    proc = subprocess.run(
        [BASH, "-e", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    recorded = dict(
        line.split("=", 1)
        for line in github_env.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return proc, recorded


pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run the step")


def _product(tmp_path: Path, dockerfile: str = "") -> tuple:
    """The checkout the build step runs in: the gate's own helpers (the
    base-image pre-pull runs them) and, when given, the product's Dockerfile;
    python3 is the runner's."""
    shutil.copytree(Path(__file__).resolve().parent, tmp_path / ".github" / "store_gate",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    if dockerfile:
        (tmp_path / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    return (("python3", f'exec "{Path(sys.executable).as_posix()}" "$@"\n'),)


def test_a_failed_build_is_recorded_and_the_step_lets_the_scorer_run(tmp_path):
    proc, recorded = _run(tmp_path, BUILD, FAILING_DOCKER, extra_shims=_product(tmp_path))

    assert proc.returncode == 0, proc.stderr
    assert recorded.get("IMAGE_BUILT") == "0"
    assert recorded.get("IMAGE_BUILD_EXIT") == "1"
    assert "parse error" in (tmp_path / "image_build.log").read_text(encoding="utf-8")


def test_a_built_image_is_recorded_as_built(tmp_path):
    proc, recorded = _run(tmp_path, BUILD, WORKING_DOCKER, extra_shims=_product(tmp_path))

    assert proc.returncode == 0, proc.stderr
    assert recorded.get("IMAGE_BUILT") == "1"
    assert recorded.get("IMAGE_BUILD_EXIT") == "0"



# -- base images from the account's own mirror (live 2026-10-09, 2026-10-13) ----
#
# Docker Hub answered 429 to the runners (2026-10-09), then ECR Public answered
# "toomanyrequests: Data limit exceeded" (2026-10-13): every public registry
# throttles anonymous runners. The product's bases come from the account's own
# ghcr.io mirror, by digest, read with the built-in token, and are given their
# upstream names before the product's own Dockerfile is built.

DIGEST = "sha256:" + "2c" * 32
MIRRORED = "sha256:" + "ab" * 32
#: Logs every call; the mirror answers a digest for every tag and every pull
#: and build succeed.
MIRROR_DOCKER = (
    'echo "$*" >> "$DOCKER_CALLS"\n'
    'case "$1 $2 $3" in\n'
    f"  'buildx imagetools inspect') echo '{{\"digest\": \"{MIRRORED}\"}}';;\n"
    "esac\n"
    "exit 0\n"
)


def _calls(tmp_path, docker, dockerfile):
    env = dict(os.environ, DOCKER_CALLS=str(tmp_path / "calls"), IMAGE_MIRROR="ghcr.io/acme/mirror",
               GHCR_USER="acme", GHCR_TOKEN="t0ken", MSYS_NO_PATHCONV="1")
    proc, recorded = _run(tmp_path, BUILD, docker, env=env, extra_shims=_product(tmp_path, dockerfile))
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    return proc, recorded, calls


@pytest.mark.skipif(os.name == "nt", reason="a Python helper runs the stand-in docker; Windows cannot exec a bash stand-in")
def test_the_product_image_builds_on_bases_pulled_from_the_mirror_by_digest(tmp_path):
    dockerfile = f"FROM node:20-slim AS console\nFROM python:3.12-slim@{DIGEST}\n"
    proc, recorded, calls = _calls(tmp_path, MIRROR_DOCKER, dockerfile)
    assert proc.returncode == 0, proc.stderr
    assert calls[0] == "login ghcr.io -u acme --password-stdin"  # the token never on argv
    # The floating tag: the mirror's digest, pulled by digest, named upstream.
    assert f"pull --quiet ghcr.io/acme/mirror/node@{MIRRORED}" in calls
    assert f"tag ghcr.io/acme/mirror/node@{MIRRORED} node:20-slim" in calls
    # The digest pin: pulled as itself, handed to BuildKit for that FROM.
    assert f"pull --quiet ghcr.io/acme/mirror/python@{DIGEST}" in calls
    build = next(c for c in calls if c.startswith("build "))
    assert f"--build-context python:3.12-slim@{DIGEST}=docker-image://ghcr.io/acme/mirror/python@{DIGEST}" in build
    assert "cerebrum-artifact:test" in build
    # Nothing is pulled from a public registry.
    assert not any(c.startswith("pull") and "ghcr.io/acme/mirror/" not in c for c in calls), calls
    assert not any("mirror.gcr.io" in c or c.startswith("buildx create") for c in calls), calls
    assert recorded.get("IMAGE_BUILT") == "1" and "IMAGE_PULL_FAILED" not in recorded


def test_the_runner_disk_is_freed_before_the_image_build():
    """Replay run 37991684239: the mirror builder keeps its own copy of the
    image and --load imports a second one; a large product image ran the
    runner out of disk ("no space left on device" importing to docker) -- a
    0/22 that judged nothing. Unused preinstalled toolchains go first."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["store-gate"]["steps"]
    names = [s.get("name") for s in steps]
    free = names.index("Free the runner's disk for the image build")
    assert free < names.index(BUILD)
    script = steps[free]["run"]
    assert "|| true" in script  # never a verdict: a path already gone is fine
    for path in ("/usr/local/lib/android", "/usr/share/dotnet"):
        assert path in script

def test_a_container_that_cannot_be_created_is_a_recorded_boot_crash(tmp_path):
    proc, recorded = _run(tmp_path, ACCEPTANCE, FAILING_DOCKER)

    assert proc.returncode == 0, proc.stderr
    assert recorded.get("BOOT_CRASHED") == "1"
    # The daemon's refusal is the evidence the boot-crash scorer reads.
    assert "invalid mount config" in (tmp_path / "container_boot.log").read_text(encoding="utf-8")


def test_a_postgres_container_that_cannot_be_created_is_recorded_as_no_answer(tmp_path):
    proc, recorded = _run(tmp_path, PG_BOOT, FAILING_DOCKER)

    assert proc.returncode == 0, proc.stderr
    assert recorded.get("STORE_POSTGRES_BOOT") == "0"
    assert "could not be created" in proc.stdout


def test_a_scan_that_did_not_run_exports_no_verdict_on_the_product(tmp_path):
    failing = "exit 1\n"
    # No python3 or pip on the runner: the install fails and neither the
    # scan nor the verdict script can run.
    proc, recorded = _run(
        tmp_path,
        AUDIT,
        WORKING_DOCKER,
        extra_shims=tuple((n, failing) for n in ("python3", "pip", "bandit", "pip-audit")),
    )

    assert proc.returncode == 0, proc.stderr
    # Unmeasured is recorded as such -- and NOT as STORE_AUDIT_CLEAN=0, which
    # the harness reads as the product's unclean code.
    assert recorded.get("STORE_AUDIT_MEASURED") == "0"
    assert "STORE_AUDIT_CLEAN" not in recorded


def test_the_acceptance_run_passes_an_unmeasured_audit_through_unset(tmp_path):
    calls = tmp_path / "docker_calls"
    # A container that builds, serves /health and runs the harness; every
    # call is logged one argument per line.
    docker = f'printf "%s\\n" "$@" >> "{calls.as_posix()}"\nexit 0\n'
    env_without_verdict = {k: v for k, v in os.environ.items() if k != "STORE_AUDIT_CLEAN"}
    proc, _ = _run(tmp_path, ACCEPTANCE, docker, env=env_without_verdict)

    assert proc.returncode == 0, proc.stderr
    args = calls.read_text(encoding="utf-8").splitlines()
    assert "STORE_AUDIT_CLEAN=" in args
    assert "STORE_AUDIT_CLEAN=0" not in args


def test_an_image_without_scripts_runs_the_harness_from_the_mounted_checkout(tmp_path):
    """Live 2026-10-08 (build/plt_28405ae4961d420b, run 37760007852): a minimal
    image (COPY app only) has no scripts/acceptance.py; the gate ran
    ``python3 scripts/acceptance.py`` inside it and the run read "workflow
    failed before scoring". The harness is the checkout's."""
    gate = tmp_path / ".github" / "store_gate"
    shutil.copytree(Path(__file__).resolve().parent, gate)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "acceptance.py").write_text(
        'REPO_ROOT_ENV = "ACCEPTANCE_REPO_ROOT"\nRUNTIME_ROOT_ENV = "ACCEPTANCE_RUNTIME_ROOT"\n',
        encoding="utf-8",
    )
    calls = tmp_path / "docker_calls"
    # The container serves /health and its working directory is /srv/app;
    # `test -f scripts/acceptance.py` fails: the image does not carry it.
    docker = (
        f'printf "%s\n" "--CALL--" "$@" >> "{calls.as_posix()}"\n'
        'case "$1" in\n'
        '  exec) for a in "$@"; do [ "$a" = pwd ] && { echo /srv/app; exit 0; }; [ "$a" = test ] && exit 1; done; exit 0;;\n'
        "  *) exit 0;;\n"
        "esac\n"
    )
    # The runner's python3 runs the gate's own helper for real.
    python = Path(sys.executable).as_posix()
    # Git Bash (Windows) would rewrite /srv/app into a Windows path on its way
    # to a native python; the gate runs on Linux, where nothing is rewritten.
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    proc, recorded = _run(
        tmp_path, ACCEPTANCE, docker,
        extra_shims=(("python3", f'exec "{python}" "$@"\n'),), env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert recorded.get("BOOT_CRASHED") != "1"
    invocations = calls.read_text(encoding="utf-8").split("--CALL--\n")
    run = next(c for c in invocations if c.startswith("run\n"))
    assert ":/repository:ro" in run, run
    harness_exec = [c for c in invocations if "python3" in c.splitlines() and "-c" not in c.splitlines()]
    assert harness_exec, invocations
    last = harness_exec[-1].splitlines()
    assert last[-1] == "/repository/scripts/acceptance.py"
    assert "ACCEPTANCE_RUNTIME_ROOT=/srv/app" in last, (last, proc.stderr[-1500:])


def test_an_older_harness_not_in_the_image_is_copied_in_and_judges_the_image(tmp_path):
    """Live 2026-10-08 (build/plt_5ac16f50c9384536, run 37767964892): the
    branch's harness declares no RUNTIME_ROOT_ENV and the minimal image does
    not carry it; run from /repository it served the CHECKOUT (which carries
    vendor/) and certified 22/22 an image that cannot load its storage block.
    It must run inside the image's runtime tree."""
    gate = tmp_path / ".github" / "store_gate"
    shutil.copytree(Path(__file__).resolve().parent, gate)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "acceptance.py").write_text(
        'REPO_ROOT_ENV = "ACCEPTANCE_REPO_ROOT"\n', encoding="utf-8"
    )
    calls = tmp_path / "docker_calls"
    docker = (
        f'printf "%s\n" "--CALL--" "$@" >> "{calls.as_posix()}"\n'
        'case "$1" in\n'
        '  exec) for a in "$@"; do [ "$a" = pwd ] && { echo /srv/app; exit 0; }; [ "$a" = test ] && exit 1; done; exit 0;;\n'
        "  *) exit 0;;\n"
        "esac\n"
    )
    python = Path(sys.executable).as_posix()
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    proc, _ = _run(
        tmp_path, ACCEPTANCE, docker,
        extra_shims=(("python3", f'exec "{python}" "$@"\n'),), env=env,
    )

    assert proc.returncode == 0, proc.stderr
    invocations = calls.read_text(encoding="utf-8").split("--CALL--\n")
    copies = [c.splitlines() for c in invocations if c.startswith("cp\n")]
    assert copies and copies[-1][-1].endswith(":/srv/app/.store-gate-harness/acceptance.py"), invocations
    harness_exec = [c.splitlines() for c in invocations if "python3" in c.splitlines() and "-c" not in c.splitlines()]
    assert harness_exec[-1][-1] == "/srv/app/.store-gate-harness/acceptance.py"
    assert not any(a.startswith("/repository/") for a in harness_exec[-1])
