"""Residential-lettings three-gate re-walk + Level grade.

A lettings brief must draft the golden roster (not a GENERATE stub), the
code cycle must emit a full 14-class repo, and a Store-green pilot is the
only path to ``pilot_ready``. The golden roster has four capabilities, so the authorship floor is 4
(not a fixed ≥5). A no-CLI / thin-authorship walk still must not claim
Store-green. Fail-closed if the pilot cycle is red.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build.store_acceptance import ACCEPTANCE_CHECK_NAMES, ACCEPTANCE_REQUIRED

from app.factory.blueprint import load_blueprint
from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES
from app.factory.build.level_grade import Level
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build_jobs import build_status
from tests.factory.offline_estate import remap_blueprint_to_estate_stubs


ROOT = Path(__file__).resolve().parents[3]
LETTINGS = ROOT / "blueprints" / "lettings" / "residential_lettings.v1.yaml"
LIVE_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "KIMI_MOCK",
        "CEREBRUM_LLM_MOCK",
    ):
        monkeypatch.delenv(var, raising=False)


def _assert_full_repo(out: Path) -> None:
    for rel in FOURTEEN_ARTIFACT_CLASSES:
        assert (out / rel).exists(), f"{rel} missing from lettings artifact"
    for rel in (
        "Dockerfile",
        "README.md",
        "app/main.py",
        "app/block_inputs.py",
        "app/actions/viewing_management.py",
        "frontend/src/App.tsx",
        "tests/test_routes.py",
        "scripts/release_gate.py",
    ):
        assert (out / rel).is_file(), f"{rel} missing"
    routes = (out / "tests" / "test_routes.py").read_text(encoding="utf-8")
    assert "def test_every_capability_route_accepts_payload" in routes
    handler = (out / "app" / "actions" / "viewing_management.py").read_text(
        encoding="utf-8"
    )
    assert "httpx" not in handler
    assert "/v1/execute" not in handler
    assert "prepare_block_input" in handler


def test_lettings_code_cycle_is_a_full_repo_and_not_pilot_ready(tmp_path, stub_coder):
    out = tmp_path / "residential-lettings"
    runner = RoleRunner(
        remap_blueprint_to_estate_stubs(load_blueprint(LETTINGS)),
        out,
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=300),
        auto_pilot=False,
    )
    outcome = runner.run()
    assert outcome.ok, outcome.to_dict()
    assert "CODE PASS" in outcome.detail
    assert "PRODUCT NOT RUN" in outcome.detail
    assert "STORE NOT RUN" in outcome.detail
    assert runner.ledger.pilot_ready() is False
    _assert_full_repo(out)

    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is False
    assert status["cycle"] == "code"
    grade = status["level_grade"]
    assert grade["level"] == Level.CODE_GREEN.value
    assert grade["founding_customer_ready"] is False
    assert grade["three_gate"]["PRODUCT"] == "NOT_RUN"
    assert grade["three_gate"]["STORE"] == "NOT_RUN"

    env = os.environ.copy()
    env["STORAGE_PATH"] = str(out / "data")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_routes.py::test_every_capability_route_accepts_payload",
            "-q",
            "--tb=line",
        ],
        cwd=out,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _docker_acceptance_kk_passthrough(argv, *, cwd=None, timeout=None):
    """CI has no Store image. Intercept docker; pass every other subprocess through.

    Authorship honesty is what this walk measures — not a host-side Store-green skip.
    """
    joined = " ".join(str(part) for part in argv)
    if argv and str(argv[0]) == "docker":
        stdout = "ok\n"
        if "acceptance.py" in joined:
            lines = [f"PASS {name} — ok" for name in ACCEPTANCE_CHECK_NAMES]
            lines.append(f"ACCEPTANCE: {ACCEPTANCE_REQUIRED}/{ACCEPTANCE_REQUIRED}")
            stdout = "\n".join(lines) + "\n"
        elif len(argv) > 1 and str(argv[1]) == "run":
            stdout = "cid\n"
        elif "-c" in argv:
            stdout = "200\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        capture_output=True,
        text=True,
        env=env,
    )


def test_lettings_three_gate_pilot_walk_is_honest(tmp_path):
    """0.5: the no-coder pilot walk refuses at the WRITER.

    The old walk passed the code cycle on factory templates and then refused
    Store-green on thin authorship. 0.5 moves the refusal earlier and makes
    it stronger: zero agent-authored artifacts is writer_no_output -- there
    is no thin SUCCESS left to be mistaken for a finished pilot. The green
    code-cycle walk (with a stubbed agent) is
    test_lettings_code_cycle_is_a_full_repo_and_not_pilot_ready.
    """
    out = tmp_path / "residential-lettings"
    lettings_offline = remap_blueprint_to_estate_stubs(load_blueprint(LETTINGS))
    code = RoleRunner(
        lettings_offline,
        out,
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=300),
        auto_pilot=False,
    ).run()
    assert code.ok is False, code.to_dict()
    assert "writer_no_output" in (code.detail or "")


def runner_pilot_ready(out: Path) -> bool:
    from app.factory.build.ledger import BuildLedger

    return BuildLedger(out / "build_ledger.jsonl").pilot_ready()
