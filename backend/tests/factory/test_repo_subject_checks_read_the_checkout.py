"""A check whose subject is the repository reads the checkout, not the image.

Live 2026-10-07 (76523740, smoke B, build/plt_9488d97f22f04dee, Store-gate run
37675692243): "store-gate: your product passed 18/18 of the checks it owns.
The Factory failed 1 of its own (ci_present_and_full_suite)" -- detail
``.github/workflows/ci.yml missing``. The branch carried ci.yml and its CI run
was green. The writer's .dockerignore leaves ``.github/`` out of the image
("VCS metadata and the CI/gate wiring are not part of the served artifact"),
a legitimate product choice, and the harness runs INSIDE the image -- so it
measured a property of the repository against the image's filesystem.
``audit_clean`` failed on the same read with a clean scan (STORE_AUDIT_CLEAN=1).

The floor already declares each check's subject. A ``tree:<path>`` or
``factory_record`` check reads REPO -- the checkout the Store gate mounts at
the path ACCEPTANCE_REPO_ROOT names -- and a ``runtime`` check still reads the
image.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.factory.build.acceptance_floor import checks, repository_check_ids
from app.factory.build.store_acceptance import (
    ACCEPTANCE_RECORD_KEY,
    ACCEPTANCE_REPO_ROOT_ENV,
    GITHUB_CI_REL,
    render_acceptance_script,
    render_github_ci,
)

#: A port nothing listens on: the HTTP checks fail fast and the harness never
#: starts a server of its own (ACCEPTANCE_BASE_URL is set).
_DEAD_BASE = "http://127.0.0.1:9"


def _image(tmp_path: Path) -> Path:
    """The image's /app: the harness, and no CI wiring (.dockerignore'd)."""
    image = tmp_path / "image"
    (image / "scripts").mkdir(parents=True)
    (image / "scripts" / "acceptance.py").write_text(render_acceptance_script(), encoding="utf-8")
    return image


def _checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    ci = checkout / GITHUB_CI_REL
    ci.parent.mkdir(parents=True)
    ci.write_text(render_github_ci(), encoding="utf-8")
    # A runtime check's file, present ONLY in the checkout: a runtime check
    # must not see it (it judges what the image carries).
    (checkout / "app").mkdir()
    (checkout / "app" / "health.py").write_text("from app import db\n", encoding="utf-8")
    return checkout


def _records(image: Path, extra_env: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k != ACCEPTANCE_REPO_ROOT_ENV}
    env.update({"ACCEPTANCE_BASE_URL": _DEAD_BASE, "PYTHONIOENCODING": "utf-8"})
    env.update(extra_env)
    out = subprocess.run(
        [sys.executable, "scripts/acceptance.py"],
        cwd=image, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    ).stdout
    records = {}
    for line in out.splitlines():
        if line.startswith("{"):
            rec = json.loads(line).get(ACCEPTANCE_RECORD_KEY)
            if rec:
                records[rec["name"]] = rec
    return records


def test_ci_present_reads_the_checkout_when_the_image_leaves_github_out(tmp_path):
    image, checkout = _image(tmp_path), _checkout(tmp_path)
    records = _records(image, {ACCEPTANCE_REPO_ROOT_ENV: str(checkout)})

    ci = records["ci_present_and_full_suite"]
    assert ci["status"] == "PASS", ci
    # The same read decided audit_clean live; it gets past the CI wiring now.
    assert ".github/workflows/ci.yml missing" not in records["audit_clean"]["detail"]


def test_a_runtime_check_still_reads_the_image(tmp_path):
    image, checkout = _image(tmp_path), _checkout(tmp_path)
    records = _records(image, {ACCEPTANCE_REPO_ROOT_ENV: str(checkout)})

    # app/health.py exists only in the checkout: the runtime check judges the
    # image, which does not carry it.
    assert records["health_fail_closed"] == {
        "name": "health_fail_closed",
        "status": "FAIL",
        "detail": "app/health.py missing",
    }


def test_unset_the_checkout_is_the_harness_s_own_tree(tmp_path):
    # The writer's --self-check and any local run: no mount, no env -- the
    # repository is where the harness sits.
    image = _image(tmp_path)
    ci = image / GITHUB_CI_REL
    ci.parent.mkdir(parents=True)
    ci.write_text(render_github_ci(), encoding="utf-8")

    assert _records(image, {})["ci_present_and_full_suite"]["status"] == "PASS"


def test_repository_checks_are_derived_from_declared_subjects():
    declared = {
        str(c["id"])
        for c in checks()
        if str(c.get("subject")).startswith("tree:") or c.get("subject") == "factory_record"
    }
    assert set(repository_check_ids()) == declared
    runtime = {str(c["id"]) for c in checks() if c.get("subject") == "runtime"}
    assert not runtime & set(repository_check_ids())
    # Rendered into the harness from the same function, with the env name the
    # gate reads from it.
    script = render_acceptance_script()
    assert "REPO_CHECKS = [%s]" % ", ".join(repr(n) for n in repository_check_ids()) in script
    assert "REPO_ROOT_ENV = %r" % ACCEPTANCE_REPO_ROOT_ENV in script
