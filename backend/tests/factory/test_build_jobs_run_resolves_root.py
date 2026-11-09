"""build_jobs._run resolves a missing blocks_root before planning and cloning."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.mark.parametrize("provided", [None, "direct"])
def test_run_uses_the_resolved_or_provided_blocks_root(tmp_path, monkeypatch, provided):
    """A falsy blocks_root is resolved (the drop_rule mutant would hand None
    through to the planner/runner); a truthy one is used as-is."""
    from app.factory import build_jobs

    blueprint = load_blueprint(SMOKE)
    resolved = tmp_path / "resolved_store"
    (resolved / "block_registry").mkdir(parents=True)
    provided_path = tmp_path / "provided_store" if provided else None
    if provided_path is not None:
        (provided_path / "block_registry").mkdir(parents=True)

    seen: dict = {}
    monkeypatch.setattr("app.factory.blocks_source.resolve_blocks_root", lambda: resolved)

    class _FakePlanner:
        def __init__(self, root, factory_shelf=None):
            seen["planner_root"] = root

        def plan(self, bp):
            return SimpleNamespace(budget_class="small")

    monkeypatch.setattr("app.factory.planner.CapabilityPlanner", _FakePlanner)
    monkeypatch.setattr("app.factory.planner.assert_generatable", lambda p: p)

    class _FakeRunner:
        def __init__(self, *a, **k):
            seen["runner_blocks_root"] = k.get("blocks_root")

        def run(self):
            from app.factory.build.runner import Outcome

            return SimpleNamespace(outcome=Outcome.SUCCESS, rework_used=0)

    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _FakeRunner)

    build_jobs._run(blueprint, tmp_path / "out", provided_path)

    want = resolved if provided is None else provided_path
    assert seen["planner_root"] == want
    assert seen["runner_blocks_root"] == want
