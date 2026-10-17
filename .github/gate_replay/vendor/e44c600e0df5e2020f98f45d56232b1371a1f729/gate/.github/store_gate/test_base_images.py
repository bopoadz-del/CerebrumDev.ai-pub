"""Base images and service images come from the account's own mirror, by digest.

Live 2026-10-13 (replay runs 38348113721, 38350862279; job 115104003598): ECR
Public answered ``toomanyrequests: Data limit exceeded`` to the runners for
the postgres service image and the products' base images. The replay legs died
at "Initialize containers" and were reported as "no score: no check failed" --
a registry throttle that named nothing.

These tests hold: what the mirror holds is derived (workflows' ``image:``
keys, Dockerfile FROM lines), never a hand list; the gate pulls from the mirror
by digest with the built-in token and gives each base its upstream name; a
pull that fails is a NAMED "image pull failed: <image>", never a verdict on
the product and never a bare "no score".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import base_images  # noqa: E402
import replay_flips  # noqa: E402

GITHUB = HERE.parent
WORKFLOWS = GITHUB / "workflows"
MIRROR = "ghcr.io/acme/mirror"
OWNER_EXPR = "${{ github.repository_owner }}"
OWNER_MIRROR = f"ghcr.io/{OWNER_EXPR}/mirror"
D = "sha256:" + "2c" * 32
E = "sha256:" + "ab" * 32
BASH = shutil.which("bash")
#: A Python helper runs the stand-in ``docker``: Windows cannot exec a bash
#: stand-in from Python (the gate runs on Linux; so does this suite's CI).
PY_RUNS_SHIMS = os.name != "nt"


def _wf(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    data["on"] = data.get("on", data.get(True))
    return data


# -- the Dockerfile's bases ------------------------------------------------------------


def test_froms_expand_global_args_and_skip_stages_and_scratch():
    dockerfile = (
        "# syntax=docker/dockerfile:1\n"
        f"ARG PYTHON_IMAGE=python:3.12-slim@{D}\n"
        'ARG NODE_IMAGE="node:20-alpine"\n'
        "FROM ${NODE_IMAGE} AS console\n"
        "FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS build\n"
        "RUN echo hi \\\n"
        "  && echo there\n"
        "FROM build AS test\n"
        "FROM scratch AS empty\n"
        "FROM ${PYTHON_IMAGE} AS runtime\n"
        "FROM ${MISSING:-python:3.11-slim}\n"
        "FROM ${UNSET}\n"
    )
    assert base_images.froms(dockerfile) == [
        "node:20-alpine",
        f"python:3.12-slim@{D}",
        "python:3.11-slim",
    ]


def test_references_split_like_docker():
    ref = base_images.parse(f"python:3.12-slim@{D}")
    assert (ref.domain, ref.path, ref.tag, ref.digest) == ("docker.io", "library/python", "3.12-slim", D)
    assert ref.familiar == f"python:3.12-slim@{D}" and ref.named == "python:3.12-slim"
    assert base_images.parse("python").tag == "latest"
    assert base_images.parse("docker.io/library/python:3.11").familiar == "python:3.11"
    assert base_images.parse("localhost:5000/x/y:1").domain == "localhost:5000"
    assert base_images.parse("bitnami/redis:7").familiar == "bitnami/redis:7"


def test_mirror_names_keep_the_official_images_together():
    # Docker Hub's library/python and ECR Public's copy of it are one image.
    assert base_images.parse("python:3.12-slim").mirror_name == "python"
    assert base_images.parse("public.ecr.aws/docker/library/python:3.11-slim-bookworm").mirror_name == "python"
    assert base_images.parse("bitnami/redis:7").mirror_name == "bitnami/redis"
    assert base_images.parse("gcr.io/distroless/base:nonroot").mirror_name == "gcr.io/distroless/base"
    # A floating tag is kept as itself; a digest pin under its own tag, which
    # the weekly refresh of the floating tag never moves.
    assert base_images.parse("node:20-slim").mirror_tag == "20-slim"
    assert base_images.parse(f"postgres:16@{D}").mirror_tag == "16-" + "2c" * 6


# -- pre-pull ---------------------------------------------------------------------------


class FakeDocker:
    """Answers like a docker CLI; records every call."""

    def __init__(self, *, digests=None, fail_pull=(), fail_inspect=False):
        self.calls: list[list[str]] = []
        self.digests = digests or {}
        self.fail_pull = fail_pull
        self.fail_inspect = fail_inspect

    def __call__(self, *args):
        self.calls.append(list(args))
        out, err, code = "", "", 0
        if args[:3] == ("buildx", "imagetools", "inspect"):
            digest = self.digests.get(args[3])
            if self.fail_inspect or not digest:
                err, code = "ERROR: manifest unknown", 1
            else:
                out = json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "digest": digest})
        elif args[0] == "pull":
            image = args[-1]
            if any(image.startswith(p) for p in self.fail_pull):
                err, code = "Error response from daemon: toomanyrequests: Data limit exceeded", 1
        return subprocess.CompletedProcess(["docker", *args], code, out, err)


def test_a_floating_tag_is_pulled_by_the_mirrors_digest_and_named_upstream(monkeypatch):
    docker = FakeDocker(digests={f"{MIRROR}/python:3.12-slim": E})
    monkeypatch.setattr(base_images, "_docker", docker)

    context, failure = base_images.prepull("python:3.12-slim", MIRROR)

    assert (context, failure) == ("", "")
    assert ["pull", "--quiet", f"{MIRROR}/python@{E}"] in docker.calls  # by digest
    assert ["tag", f"{MIRROR}/python@{E}", "python:3.12-slim"] in docker.calls
    assert not any(c[0] == "pull" and not c[-1].startswith(MIRROR) for c in docker.calls)


def test_a_digest_pin_is_pulled_as_itself_and_handed_to_the_build_as_a_named_context(monkeypatch):
    """A tag cannot stand for a digest: ``FROM python:3.12-slim@sha256:...``
    would still ask Docker Hub. BuildKit takes the pulled image in its place
    (verified on a runner with Docker Hub blocked: the tag resolves locally,
    the digest only through the named context)."""
    docker = FakeDocker()
    monkeypatch.setattr(base_images, "_docker", docker)

    context, failure = base_images.prepull(f"python:3.12-slim@{D}", MIRROR)

    assert failure == ""
    assert context == f"python:3.12-slim@{D}=docker-image://{MIRROR}/python@{D}"
    assert docker.calls == [["pull", "--quiet", f"{MIRROR}/python@{D}"]]  # no tag lookup


def test_a_base_not_yet_mirrored_comes_from_its_own_registry(monkeypatch):
    docker = FakeDocker()  # the mirror answers no digest for the tag
    monkeypatch.setattr(base_images, "_docker", docker)

    context, failure = base_images.prepull("node:22-slim", MIRROR)

    assert (context, failure) == ("", "")
    assert docker.calls[-1] == ["pull", "--quiet", "node:22-slim"]


def test_a_base_that_cannot_be_pulled_anywhere_is_named(monkeypatch, tmp_path):
    docker = FakeDocker(digests={f"{MIRROR}/python:3.12-slim": E}, fail_pull=(MIRROR, "python"))
    monkeypatch.setattr(base_images, "_docker", docker)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    code = base_images.run_prepull(tmp_path / "Dockerfile", MIRROR, tmp_path / "contexts")

    assert code == 1
    named = (tmp_path / base_images.PULL_FAILED).read_text(encoding="utf-8")
    assert named.startswith("image pull failed: python:3.12-slim (")
    assert "toomanyrequests" in named  # the registry's answer is the evidence
    assert not any(c[0] == "tag" for c in docker.calls)


def test_a_product_without_a_dockerfile_is_left_to_its_build(monkeypatch, tmp_path):
    monkeypatch.setattr(base_images, "_docker", FakeDocker())
    monkeypatch.chdir(tmp_path)
    assert base_images.run_prepull(tmp_path / "Dockerfile", MIRROR, tmp_path / "contexts") == 0
    assert not (tmp_path / base_images.PULL_FAILED).exists()


# -- the mirror's list is derived ---------------------------------------------------------


def test_the_mirror_list_is_derived_from_workflows_and_dockerfiles(tmp_path):
    dockerfile = tmp_path / "1.Dockerfile"
    dockerfile.write_text(f"ARG P=python:3.12-slim@{D}\nFROM ${{P}} AS runtime\n", encoding="utf-8")
    refs = base_images.derive(
        MIRROR, sorted(WORKFLOWS.glob("*.yml")) + [dockerfile]
    )
    services = _service_images()
    assert services, "the gate's postgres is expected"
    for image in services.values():
        assert image.replace(OWNER_MIRROR + "/", "") in refs
    assert f"python:3.12-slim@{D}" in refs


def test_the_mirror_holds_only_this_repositorys_own_images():
    """A ghcr package is readable by the repository that published it; every
    other repository publishes its own mirror, so no hand list of another
    repository's images is kept here."""
    assert not (GITHUB / "image_mirror.json").exists()
    assert "image_mirror.json" not in (WORKFLOWS / "image-mirror.yml").read_text(encoding="utf-8")


def test_the_copy_plan_prefers_sources_not_under_docker_hubs_anonymous_limit():
    rows = base_images.plan(MIRROR, ["python:3.12-slim", f"postgres:16@{D}", "gcr.io/distroless/base:nonroot"])
    py, pg, distroless = rows
    assert py["sources"].split() == [
        "mirror.gcr.io/library/python",
        "public.ecr.aws/docker/library/python",
        "docker.io/library/python",
    ]
    assert (py["mirror"], py["mirror_tag"], py["digest"]) == (f"{MIRROR}/python", "3.12-slim", "")
    assert (pg["mirror"], pg["digest"]) == (f"{MIRROR}/postgres", D)
    assert distroless["sources"] == "gcr.io/distroless/base"


def test_a_copy_is_read_back_digest_for_digest(monkeypatch):
    class Mirror(FakeDocker):
        def __call__(self, *args):
            if args[:3] == ("buildx", "imagetools", "create"):
                target, source = args[4], args[5]
                if source.startswith("mirror.gcr.io"):
                    self.calls.append(list(args))
                    return subprocess.CompletedProcess(args, 1, "", "ERROR: 503")
                self.digests[target] = source.split("@", 1)[1]
            return super().__call__(*args)

    docker = Mirror(digests={"mirror.gcr.io/library/python:3.12-slim": E})
    monkeypatch.setattr(base_images, "_docker", docker)
    row = base_images.copy(base_images.plan(MIRROR, ["python:3.12-slim"])[0])
    assert row["error"] == "" and row["mirrored"] == f"{MIRROR}/python@{E}"
    # The first source failed the copy; the next one served the same digest.
    assert ["buildx", "imagetools", "create", "--tag", f"{MIRROR}/python:3.12-slim",
            f"public.ecr.aws/docker/library/python@{E}"] in docker.calls


def test_a_copy_the_mirror_does_not_answer_with_the_same_digest_fails(monkeypatch):
    class Drifting(FakeDocker):
        def __call__(self, *args):
            if args[:3] == ("buildx", "imagetools", "create"):
                self.digests[args[4]] = "sha256:" + "00" * 32
            return super().__call__(*args)

    monkeypatch.setattr(base_images, "_docker", Drifting())
    row = base_images.copy(base_images.plan(MIRROR, [f"python:3.12-slim@{D}"])[0])
    assert row["mirrored"] == "" and "not the copied" in row["error"]


# -- the workflows -------------------------------------------------------------------------


def _service_images() -> dict:
    """The gate's own service images, ``GATE_SERVICE_IMAGES`` in the job env."""
    job = _wf("store-gate.yml")["jobs"]["store-gate"]
    return dict(base_images.service_pairs(str(job["env"][base_images.SERVICE_IMAGES_ENV])))


def test_no_job_pulls_a_service_or_container_before_its_steps_run():
    """A `services:`/`container:` image is pulled at "Initialize containers",
    before any step can say why it failed (live run 38351334962)."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_name, job in (_wf(path.name).get("jobs") or {}).items():
            assert not job.get("services") and not job.get("container"), (path.name, job_name)


def test_every_gate_service_image_comes_from_the_mirror_by_digest():
    images = _service_images()
    assert "postgres" in images
    for name, image in images.items():
        assert image.startswith(OWNER_MIRROR + "/"), (name, image)
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), (name, image)


def test_postgres_is_pulled_by_a_step_with_the_built_in_token():
    step = _step("Start Postgres (gate infrastructure)")
    assert "base_images.py pull" in step["run"] and "GATE_SERVICE_IMAGES" in step["run"]
    # Read with the built-in token -- no new secret.
    assert step["env"]["GHCR_TOKEN"] == "${{ github.token }}" and "--password-stdin" in step["run"]
    assert step["env"]["IMAGE_MIRROR"] == OWNER_MIRROR


def test_the_gate_reads_the_mirror_with_the_built_in_token_only():
    gate = _wf("store-gate.yml")
    assert gate["permissions"]["packages"] == "read"
    replay = _wf("gate-replay.yml")["jobs"]["replay"]
    assert replay["permissions"]["packages"] == "read"  # a called workflow gets no more than its caller
    build = next(s for s in gate["jobs"]["store-gate"]["steps"] if s.get("name") == "Build artifact image")
    assert build["env"]["GHCR_TOKEN"] == "${{ github.token }}"
    assert build["env"]["IMAGE_MIRROR"] == OWNER_MIRROR
    assert "--password-stdin" in build["run"] and "$GHCR_TOKEN" not in build["run"].split("|", 1)[1]
    assert "mirror.gcr.io" not in build["run"] and "buildx create" not in build["run"]
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "ghcr.io" in text:
            assert "secrets." not in text, path.name


def test_the_mirror_workflow_runs_weekly_and_on_demand_with_the_built_in_token():
    wf = _wf("image-mirror.yml")
    assert "workflow_dispatch" in wf["on"] and wf["on"]["schedule"]
    assert wf["permissions"] == {"contents": "read", "packages": "write"}
    text = (WORKFLOWS / "image-mirror.yml").read_text(encoding="utf-8")
    assert "secrets." not in text
    job = wf["jobs"]["mirror"]
    assert job["env"]["IMAGE_MIRROR"] == OWNER_MIRROR
    runs = "\n".join(str(s.get("run") or "") for s in job["steps"])
    # Derived: the workflows and every branch's Dockerfile.
    assert "base_images.py derive" in runs and ".github/workflows/*.yml" in runs
    assert "refs/remotes/origin" in runs
    assert "base_images.py mirror" in runs
    login = next(s for s in job["steps"] if "docker login" in str(s.get("run")))
    assert login["env"]["GHCR_TOKEN"] == "${{ github.token }}" and "--password-stdin" in login["run"]
    checkout = job["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0


# -- the "no score" names the image ----------------------------------------------------------


def test_a_replay_that_could_not_pull_names_the_image_never_no_check_failed():
    result = {
        "ok": False,
        "score": "no score (image pull failed: postgres)",
        "lines": [],
        "via": "image-pull-failed",
        "detail": "image pull failed: ghcr.io/acme/mirror/postgres:16@sha256:x (service postgres)",
    }
    why = replay_flips.flip(True, result)
    assert "image pull failed: ghcr.io/acme/mirror/postgres" in why and "no check failed" not in why
    missed = replay_flips.missed("cross_tenant_404", result)
    assert "image pull failed" in missed and "no check failed" not in missed


PARSE = "Parse k/N and write result"
BUILD = "Build artifact image"


def _step(name: str) -> dict:
    for step in _wf("store-gate.yml")["jobs"]["store-gate"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(name)


def _run(tmp_path: Path, name: str, env_extra: dict, docker: str = "exit 1\n") -> tuple:
    shims = tmp_path / "bin"
    shims.mkdir(exist_ok=True)
    for tool, body in (("python3", f'exec "{Path(sys.executable).as_posix()}" "$@"\n'), ("docker", docker)):
        path = shims / tool
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    github_env = tmp_path / "github_env"
    github_env.write_text("", encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(_step(name)["run"].replace("${{ github.sha }}", "test"), encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env["PATH"] = f"{shims}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_ENV"] = str(github_env)
    env["MSYS_NO_PATHCONV"] = "1"
    env.update(env_extra)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    proc = subprocess.run([BASH, "-e", str(script)], cwd=work, env=env, capture_output=True, text=True, timeout=120)
    recorded = dict(l.split("=", 1) for l in github_env.read_text(encoding="utf-8").splitlines() if "=" in l)
    return proc, recorded, work


def _service_env() -> dict:
    return {"GITHUB_SHA": "f" * 40, "REPLAY_LABEL": "x"}


@pytest.mark.skipif(BASH is None, reason="bash is required to run the step")
def test_a_service_whose_image_never_pulled_is_named_by_the_scorer(tmp_path):
    """Job 115104003598: the postgres pull failed and the result read "no
    score" naming nothing. The Start Postgres step now names the image."""
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "image_pull_failed.txt").write_text(
        "image pull failed: ghcr.io/acme/mirror/postgres:16@sha256:x (ghcr.io/acme/mirror/postgres@sha256:x: unauthorized)\n",
        encoding="utf-8",
    )
    proc, _, work = _run(tmp_path, PARSE, _service_env())
    assert proc.returncode == 0, proc.stderr
    report = json.loads((work / "store_gate.json").read_text(encoding="utf-8"))
    assert report["ok"] is False and (report["passed"], report["total"]) == (0, 0)
    assert report["via"] == "image-pull-failed"
    assert report["score"].startswith("no score (image pull failed: ghcr.io/acme/mirror/postgres:16@sha256:")
    # Never a k/N the Factory could bill the product with.
    assert not re.search(r"\d+\s*/\s*\d+", f"acceptance.py in Docker {report['score']}")


@pytest.mark.skipif(BASH is None, reason="bash is required to run the step")
def test_a_base_image_that_never_pulled_is_named_by_the_scorer(tmp_path):
    (tmp_path / "work").mkdir()
    _gate_tree(tmp_path / "work")
    (tmp_path / "work" / "image_pull_failed.txt").write_text(
        "image pull failed: python:3.12-slim (mirror: x; upstream: toomanyrequests: Data limit exceeded)\n",
        encoding="utf-8",
    )
    proc, _, work = _run(tmp_path, PARSE, _service_env())
    report = json.loads((work / "store_gate.json").read_text(encoding="utf-8"))
    # Throttled, by the registry's own code: named as infrastructure.
    assert report["score"] == "no score (infra: image pull rate-limited)"
    assert "python:3.12-slim" in report["detail"] and "toomanyrequests" in report["detail"]
    assert report["via"] == "image-pull-failed"


@pytest.mark.skipif(BASH is None, reason="bash is required to run the step")
def test_started_services_and_pulled_bases_are_scored_as_always(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "acceptance.out").write_text("PASS a - ok\nACCEPTANCE: 1/1\n", encoding="utf-8")
    proc, _, work = _run(tmp_path, PARSE, _service_env())
    report = json.loads((work / "store_gate.json").read_text(encoding="utf-8"))
    assert report["score"] == "1/1" and report["ok"] is True and report["via"] != "image-pull-failed"


def _gate_tree(work: Path) -> None:
    shutil.copytree(HERE, work / ".github" / "store_gate", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))


@pytest.mark.skipif(BASH is None or not PY_RUNS_SHIMS, reason="bash, and a Python that can exec a stand-in docker")
def test_the_build_step_records_a_pull_failure_and_never_builds(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    _gate_tree(work)
    (work / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    calls = tmp_path / "calls"
    docker = (
        f'printf "%s\\n" "--CALL--" "$@" >> "{calls.as_posix()}"\n'
        'case "$1" in\n'
        "  login) exit 0;;\n"
        "  pull) echo 'Error response from daemon: toomanyrequests: Data limit exceeded' >&2; exit 1;;\n"
        "  *) exit 1;;\n"
        "esac\n"
    )
    proc, recorded, _ = _run(tmp_path, BUILD, {"IMAGE_MIRROR": MIRROR, "GHCR_TOKEN": "t0ken", "GHCR_USER": "u"}, docker)

    assert proc.returncode == 0, proc.stderr
    assert recorded.get("IMAGE_PULL_FAILED") == "1"
    assert "IMAGE_BUILT" not in recorded  # neither built nor scored as the product's
    assert "image pull failed: python:3.12-slim" in (work / "image_pull_failed.txt").read_text(encoding="utf-8")
    invocations = calls.read_text(encoding="utf-8").split("--CALL--\n")
    assert not any(c.startswith("build\n") for c in invocations)
    assert "t0ken" not in calls.read_text(encoding="utf-8")  # the token goes through stdin only
