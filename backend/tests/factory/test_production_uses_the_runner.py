"""Production builds through the role runner, not the template path.

New-shape tests for the cutover. The defect these close was found by
physically downloading a product from the live platform: it contained six
handlers that were ONE template differing only in a ``BLOCK_IDS`` list,
each dispatching to the operator's block store over HTTP
(``store_url + "/v1/execute"``), with the vendored blocks present but
unreachable -- no dispatch runtime, no lockfile, no Store runtime slice.

Every gate, agent wiring and offline guarantee proven during the factory
campaign lived in ``app.factory.build`` (the role runner), which no
production door called. ``generate_product`` is that door; these tests
assert it now leads to the runner, that a mid-build or failed artifact
cannot be downloaded, and that the template path stays reachable as an
explicit revert.

No LLM key and no network: the coder is disabled, so the runner exercises
its deterministic path here. That is the same route CI has always run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build_jobs import (
    RUNNER,
    TEMPLATE,
    build_engine,
    build_status,
    is_build_complete,
    start_runner_build,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_BUILD_ENGINE", raising=False)


def _await_build(out: Path, timeout_s: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = build_status(out)
        if status["state"] in ("succeeded", "failed"):
            return status
        time.sleep(0.5)
    pytest.fail(f"build did not finish in {timeout_s}s: {build_status(out)}")


def test_the_runner_is_the_default_engine():
    """The whole point of the cutover: nothing needs to be set for a
    production build to be agent-manufactured."""
    assert build_engine() == RUNNER


def test_production_floor_budget_is_a_code_phase(monkeypatch):
    """Without a factory key the Floor still gates a 20–30 min code pass.

    When the coder is keyed, ``factory_auto_pilot_enabled`` starts the
    staged 30 min stop-and-inspect wall (see test_auto_pilot).
    """
    from app.factory import build_jobs

    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_BUILD_MAX_REWORK", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    assert build_jobs._wall_clock_s() == 1800.0
    # Owner rule: 2 rework rounds per gate (REWORK_BUDGET).
    from app.factory.build.runner import REWORK_BUDGET

    assert build_jobs._max_rework() == REWORK_BUDGET == 2
    assert build_jobs._phase_wall_clock_s() == 1500.0


def test_template_engine_is_still_reachable_as_a_revert(monkeypatch):
    """A build regression in production must be revertible by env alone."""
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
    assert build_engine() == TEMPLATE
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "legacy")
    assert build_engine() == TEMPLATE


def test_generate_product_routes_to_the_runner(tmp_path, monkeypatch):
    """``generate_product`` is the single door every production caller uses;
    it must not reach ProductGenerator while the runner engine is on."""
    from app.factory import product_architect

    called = {}

    class _Boom:
        def __init__(self, *a, **k):
            called["template"] = True
            raise AssertionError("the template generator was used")

    monkeypatch.setattr(product_architect, "ProductGenerator", _Boom)
    forwarded = {}

    def runner(bp, out, **kwargs):
        forwarded.update(kwargs)
        return {"engine": RUNNER, "output_dir": str(out)}

    monkeypatch.setattr("app.factory.build_jobs.start_runner_build", runner)

    result = product_architect.generate_product(
        load_blueprint(SMOKE), tmp_path / "out", platform_id="plt_0123456789abcdef"
    )
    assert result["engine"] == RUNNER
    assert "template" not in called
    # The runner receives the platform it builds and is never told to start
    # over unless the caller (only the typed Start over) asked.
    assert forwarded["platform_id"] == "plt_0123456789abcdef"
    assert forwarded["start_over"] is False


def test_a_finished_build_carries_agent_manufactured_shape(tmp_path, stub_coder):
    """The artifact the customer receives must be a FULL platform repo.

    Workspace + download zip both carry the 14-class contract (README,
    app entrypoints, frontend, kits, product-dna, vendored blocks,
    release gate) plus local dispatch and a lockfile. A parts list
    (app/ + vendor/ only) is not a delivered platform.

    Every one of these was ABSENT from the physically downloaded product.
    """
    out = tmp_path / "build"
    started = start_runner_build(load_blueprint(SMOKE), out)
    assert started["engine"] == RUNNER
    assert started["build"]["state"] in ("building", "succeeded")

    status = _await_build(out)
    assert status["state"] == "succeeded", status
    assert is_build_complete(out)

    for required in (
        "app/main.py",
        "app/dispatch.py",
        "app/models.py",
        "app/store.py",
        "app/routes.py",
        "app/jobs.py",
        "blocks.lock.json",
        "tests/test_smoke.py",
        "Dockerfile",
        # The clone-and-test promise the template path has always shipped:
        # the cutover must not quietly drop a customer-facing contract.
        "scripts/release_gate.py",
        "docs/build_provenance.json",
    ):
        assert (out / required).is_file(), f"{required} missing from the artifact"

    # The template path's signature failure: an HTTP callback to the store.
    for handler in (out / "app" / "actions").glob("*.py"):
        source = handler.read_text(encoding="utf-8")
        assert "httpx" not in source, f"{handler.name} calls out over HTTP"
        assert "/v1/execute" not in source, f"{handler.name} calls the store"

    lock = json.loads((out / "blocks.lock.json").read_text(encoding="utf-8"))
    assert lock["blocks"], "nothing was pinned"

    # Provenance must record which artifacts the agent wrote. With the coder
    # disabled here every source is the deterministic template -- the point
    # is that the record EXISTS and is honest, not that an agent ran.
    prov = json.loads(
        (out / "docs" / "build_provenance.json").read_text(encoding="utf-8")
    )
    assert prov["engine"] == "role_runner"
    assert prov["artifact_sources"], "no artifact provenance recorded"

    gate = (out / "scripts" / "release_gate.py").read_text(encoding="utf-8")
    assert "blocks.lock.json" in gate, "the gate does not audit block provenance"
    assert "coder LLM" in gate, "the gate does not report agent authorship"

    # The customer download is this zip — not the on-disk workspace. A parts
    # list that only exists on disk is not a delivered platform.
    import zipfile

    from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES
    from app.routers.session_product import zip_generated_product

    zpath = zip_generated_product(out, tmp_path / "pilot-export")
    names = zipfile.ZipFile(zpath).namelist()
    for rel in FOURTEEN_ARTIFACT_CLASSES:
        prefix = rel.rstrip("/")
        assert any(n == prefix or n.startswith(prefix + "/") for n in names), (
            f"{rel} missing from the download zip"
        )
    for rel in (
        "README.md",
        "Dockerfile",
        "requirements.txt",
        "blocks.lock.json",
        "app/main.py",
        "frontend/src/App.tsx",
        "scripts/release_gate.py",
        "product-dna/checksum_manifest.json",
    ):
        assert rel in names, f"{rel} missing from the download zip"
    assert any(n.startswith("kits/") and n.endswith("manifest.json") for n in names), (
        "kits/ pack manifests missing from the download zip"
    )
    assert any(n.startswith("vendor/blocks/") for n in names)
    assert not any("__pycache__" in n for n in names)


def test_status_is_read_from_the_ledger_not_process_memory(tmp_path, stub_coder):
    """Status must survive a worker restart, so it is a read of the
    artifact. A build directory with no ledger is 'unknown', never a crash."""
    assert build_status(tmp_path / "nothing-here")["state"] == "unknown"

    out = tmp_path / "build"
    start_runner_build(load_blueprint(SMOKE), out)
    _await_build(out)

    # Nothing of this process's state is consulted: a fresh read of the same
    # directory answers succeeded.
    assert build_status(out)["state"] == "succeeded"
    assert build_status(out)["phases_done"] == build_status(out)["phases_total"]


def test_a_mid_build_or_failed_product_is_not_downloadable(tmp_path):
    """Zipping mid-build ships a splice of two writer passes; zipping a
    failed build ships an artifact the gates rejected. Both must be refused,
    which is what ``is_build_complete`` gates the download on."""
    from app.factory.build.ledger import BuildLedger, EventKind

    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="probe", inputs_hash="abc")
    assert build_status(out)["state"] == "building"
    assert not is_build_complete(out)

    ledger.append(EventKind.RUN_FAILED, detail="TESTER gate failed")
    status = build_status(out)
    assert status["state"] == "failed"
    assert not is_build_complete(out)
    assert "TESTER" in status["detail"]
