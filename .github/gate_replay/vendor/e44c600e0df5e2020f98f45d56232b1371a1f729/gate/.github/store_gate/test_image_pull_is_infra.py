"""A registry that throttles the gate is infrastructure, named as such.

Live 2026-10-13 (build/plt_70300d9a0c2b400b, run 38351334962, cycle 10 co-op):
the Postgres SERVICE container could not be pulled --
``public.ecr.aws/docker/library/postgres:16``: "toomanyrequests: Data limit
exceeded" -- so GitHub failed "Initialize containers" before any step ran. No
step could record why: the status read "acceptance.py in Docker no score (the
image or the script did not run)" and the Factory told the owner "the
Factory's own workflow failed before scoring".

One mechanism, with the account's ghcr mirror (base_images.py):

* Postgres is started by a step, never a ``services:`` container: the
  mirror's pinned digest first (built-in token), then the same digest from
  Google's mirror, ECR Public and Docker Hub -- one throttled registry is not a
  stopped gate;
* every source failing writes ``image_pull_failed.txt`` naming the image and
  each registry's answer; throttled (the registry's own ``toomanyrequests``)
  the score reads "no score (infra: image pull rate-limited)";
* an unscored pull failure is written as GitHub's ``error`` (the check could
  not run), never ``failure`` -- the Factory carries it to the owner as
  infrastructure, never as the product's failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import base_images  # noqa: E402

WORKFLOW = HERE.parent / "workflows" / "store-gate.yml"
PARSE = "Parse k/N and write result"
STATUS = "Write commit status"
MIRROR = "ghcr.io/acme/mirror"
D = "sha256:" + "2c" * 32
PG = f"{MIRROR}/postgres:16@{D}"
THROTTLED = "Error response from daemon: toomanyrequests: Data limit exceeded"
BASH = shutil.which("bash")


def _step(name: str) -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in doc["jobs"]["store-gate"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(name)


class Registry:
    """A docker CLI whose pulls fail for the listed prefixes, as a registry
    that throttles the runner answers."""

    def __init__(self, throttled=()):
        self.throttled = throttled
        self.calls: list[list[str]] = []

    def __call__(self, *args):
        self.calls.append(list(args))
        if args[0] == "pull" and any(args[-1].startswith(p) for p in self.throttled):
            return subprocess.CompletedProcess(["docker", *args], 1, "", THROTTLED)
        return subprocess.CompletedProcess(["docker", *args], 0, "", "")


def _pulls(registry: Registry) -> list[str]:
    return [c[-1] for c in registry.calls if c[0] == "pull"]


def test_the_mirror_is_tried_first_by_digest(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    registry = Registry()
    monkeypatch.setattr(base_images, "_docker", registry)
    assert base_images.pull_service(PG, MIRROR, "gate-postgres:16") == ""
    assert _pulls(registry) == [f"{MIRROR}/postgres@{D}"]
    assert ["tag", f"{MIRROR}/postgres@{D}", "gate-postgres:16"] in registry.calls


def test_a_throttled_registry_falls_back_to_the_same_digest_elsewhere(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    registry = Registry(throttled=(MIRROR, "mirror.gcr.io"))
    monkeypatch.setattr(base_images, "_docker", registry)
    assert base_images.pull_service(PG, MIRROR, "gate-postgres:16") == ""
    assert _pulls(registry) == [
        f"{MIRROR}/postgres@{D}",
        f"mirror.gcr.io/library/postgres@{D}",
        f"public.ecr.aws/docker/library/postgres@{D}",
    ]
    assert not (tmp_path / base_images.PULL_FAILED).exists()


def test_every_source_throttled_is_named_rate_limited(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(base_images, "_docker", Registry(throttled=("",)))
    failure = base_images.pull_service(PG, MIRROR, "gate-postgres:16")
    written = (tmp_path / base_images.PULL_FAILED).read_text(encoding="utf-8")
    assert failure and written.startswith(f"image pull failed: {PG} (")
    assert base_images.pull_failure_cause(written) == "image pull rate-limited"
    assert base_images.pull_failure_cause("image pull failed: x (manifest unknown)") == "image pull failed"


def test_the_pull_command_exits_non_zero_so_nothing_is_scored(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(base_images, "_docker", Registry(throttled=("",)))
    assert base_images.main(["pull", PG, MIRROR, "gate-postgres:16"]) == 1


@pytest.mark.skipif(BASH is None, reason="bash is required to run the step")
def test_the_status_names_the_infra_cause(tmp_path):
    (tmp_path / ".github").mkdir()
    shutil.copytree(HERE, tmp_path / ".github" / "store_gate", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "image_pull_failed.txt").write_text(
        f"image pull failed: {PG} ({MIRROR}/postgres@{D}: {THROTTLED})\n", encoding="utf-8"
    )
    script = tmp_path / "parse.sh"
    script.write_text(_step(PARSE)["run"], encoding="utf-8", newline="\n")
    env = dict(os.environ, REPLAY_LABEL="x", PRODUCT_SHA="")
    proc = subprocess.run([BASH, "-e", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    report = json.loads((tmp_path / "store_gate.json").read_text(encoding="utf-8"))
    assert report["score"] == "no score (infra: image pull rate-limited)", proc.stderr
    assert report["ok"] is False and report["lines"] == []
    assert report["via"] == "image-pull-failed"
    assert len(f"acceptance.py in Docker {report['score']}") <= 140


def test_an_unscored_pull_failure_is_written_as_error_not_failure():
    """GitHub's "error" state is the typed signal the Factory reads: the check
    could not run. "failure" stays a verdict."""
    body = _step(STATUS)["run"]
    assert "state=error" in body and '"image-pull-failed"' in body
