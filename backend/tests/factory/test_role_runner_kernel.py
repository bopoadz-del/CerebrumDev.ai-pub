"""U12: role_runner must ship cerebrum_product_kernel and route through it."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_role_runner_vendors_kernel_and_routes_through_execute_action(
    tmp_path, stub_coder
):
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()

    runtime = out / "app" / "cerebrum_product_kernel" / "contract" / "runtime.py"
    assert runtime.is_file(), "role_runner must vendor cerebrum_product_kernel"
    assert "def execute_action" in runtime.read_text(encoding="utf-8")

    bridge = (out / "app" / "kernel_bridge.py").read_text(encoding="utf-8")
    assert "execute_action" in bridge
    assert "run_capability" in bridge

    routes = (out / "app" / "routes.py").read_text(encoding="utf-8")
    assert "from app.kernel_bridge import run_capability" in routes
    assert "await run_capability" in routes
    assert "async def analytics_surface_create" in routes
