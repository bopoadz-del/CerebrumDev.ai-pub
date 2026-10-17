"""The acceptance harness runs from the CHECKOUT, against the image's runtime.

Live 2026-10-08 (94d40722, cycle 2 smoke A, build/plt_28405ae4961d420b,
Store-gate run 37760007852): ``python3: can't open file
'/app/scripts/acceptance.py': [Errno 2] No such file or directory`` -- "store-
gate did not run". The writer's Dockerfile builds a minimal production image
(``COPY app``, ``COPY alembic``, ``COPY alembic.ini``), a legitimate product
choice; the gate assumed the Factory's harness was INSIDE the image.

The harness is a repository artifact (Factory-rendered, Factory-owned). The
gate mounts the checkout and runs it from there, telling it where the image's
runtime tree is through RUNTIME_ROOT_ENV -- the env NAME is the harness's own
constant, read by the gate, so the two sides hold one name. Unset (the
writer's --self-check, a local run) the runtime tree is the harness's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.factory.build.store_acceptance import (
    ACCEPTANCE_RECORD_KEY,
    ACCEPTANCE_REPO_ROOT_ENV,
    ACCEPTANCE_RUNTIME_ROOT_ENV,
    GITHUB_CI_REL,
    render_acceptance_script,
    render_github_ci,
)

#: A port nothing listens on: HTTP checks fail fast, no server is started.
_DEAD_BASE = "http://127.0.0.1:9"


def _layout(tmp_path: Path):
    """An image WITHOUT scripts/ (minimal Dockerfile) and the checkout that
    carries the harness and the CI wiring."""
    image = tmp_path / "image"
    (image / "app").mkdir(parents=True)
    # A runtime file present ONLY in the image: the runtime check must see it.
    (image / "app" / "health.py").write_text("from app import db\n", encoding="utf-8")
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "acceptance.py").write_text(render_acceptance_script(), encoding="utf-8")
    ci = checkout / GITHUB_CI_REL
    ci.parent.mkdir(parents=True)
    ci.write_text(render_github_ci(), encoding="utf-8")
    return image, checkout


def _records(harness: Path, cwd: Path, extra_env: dict) -> dict:
    env = {
        k: v for k, v in os.environ.items()
        if k not in (ACCEPTANCE_REPO_ROOT_ENV, ACCEPTANCE_RUNTIME_ROOT_ENV)
    }
    env.update({"ACCEPTANCE_BASE_URL": _DEAD_BASE, "PYTHONIOENCODING": "utf-8"})
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(harness)],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )
    records = {}
    for line in proc.stdout.splitlines():
        if line.startswith("{"):
            rec = json.loads(line).get(ACCEPTANCE_RECORD_KEY)
            if rec:
                records[rec["name"]] = rec
    assert records, proc.stdout[-2000:] + proc.stderr[-2000:]
    return records


def test_harness_from_the_checkout_judges_the_image_runtime(tmp_path):
    image, checkout = _layout(tmp_path)
    records = _records(
        checkout / "scripts" / "acceptance.py",
        image,
        {ACCEPTANCE_REPO_ROOT_ENV: str(checkout), ACCEPTANCE_RUNTIME_ROOT_ENV: str(image)},
    )
    # The runtime check read the IMAGE's app/health.py (absent from the
    # checkout): it is judged, not reported missing.
    assert records["health_fail_closed"]["detail"] != "app/health.py missing", records["health_fail_closed"]
    # The repository check still reads the checkout.
    assert records["ci_present_and_full_suite"]["status"] == "PASS", records["ci_present_and_full_suite"]


def test_unset_the_runtime_is_the_harness_s_own_tree(tmp_path):
    # The writer's --self-check / a local run: harness and product share a
    # tree, no env. The image-only file is invisible -- the runtime is where
    # the harness sits (the checkout here).
    image, checkout = _layout(tmp_path)
    records = _records(checkout / "scripts" / "acceptance.py", checkout, {})
    assert records["health_fail_closed"]["detail"] == "app/health.py missing"


def test_the_gate_reads_the_runtime_env_name_from_the_harness():
    script = render_acceptance_script()
    assert "RUNTIME_ROOT_ENV = %r" % ACCEPTANCE_RUNTIME_ROOT_ENV in script
    assert ACCEPTANCE_RUNTIME_ROOT_ENV != ACCEPTANCE_REPO_ROOT_ENV
