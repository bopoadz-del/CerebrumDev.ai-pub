"""build_jobs._run post-build guard: a failed run refunds quota and returns."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.mark.parametrize("provided", [None, "direct"])
def test_run_refunds_quota_on_a_failed_build(tmp_path: Path, monkeypatch, provided):
    """A RUN_FAILED terminal refunds the generation quota and returns; the
    drop_rule mutant removes the guard and clones canonical instead."""
    from app.factory import build_jobs
    from app.factory.build.ledger import BuildLedger, EventKind

    blueprint = load_blueprint(SMOKE)
    out = tmp_path / "out"
    if provided:
        provided_root = tmp_path / provided
        (provided_root / "block_registry").mkdir(parents=True)
    else:
        provided_root = None

    # Pre-write a RUN_FAILED terminal so the post-build guard sees a failure.
    ledger = BuildLedger(build_jobs._ledger_path(out))
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(EventKind.RUN_FAILED, detail="failed")

    calls: list[str] = []
    monkeypatch.setattr(build_jobs, "_refund_generation_quota", lambda o: calls.append("refund"))
    monkeypatch.setattr(build_jobs, "_maybe_clone_canonical", lambda *a, **k: calls.append("clone"))

    class _FakePlanner:
        def __init__(self, root, factory_shelf=None):
            pass

        def plan(self, bp):
            return SimpleNamespace(budget_class="small")

    monkeypatch.setattr("app.factory.planner.CapabilityPlanner", _FakePlanner)
    monkeypatch.setattr("app.factory.planner.assert_generatable", lambda p: p)

    class _FakeRunner:
        def __init__(self, *a, **k):
            pass

        def run(self):
            from app.factory.build.runner import Outcome

            return SimpleNamespace(outcome=Outcome.SUCCESS, rework_used=0)

    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _FakeRunner)

    build_jobs._run(blueprint, out, provided_root)

    assert "refund" in calls
    assert "clone" not in calls
