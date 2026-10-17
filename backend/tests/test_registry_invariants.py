"""Registry invariants: every documented router mounts, and feature-flagged
kernels report honest disabled status by default.

This is a CI meta-test: it runs automatically once it lands because ci.yml
runs full pytest on push to master.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.factory.platform_chat_flow as platform_chat_flow
import app.main
from app.change_requests.flags import change_request_intake_enabled
from app.factory.blueprint import ProductBlueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.planner import CapabilityPlanner
from app.resident_engineer.flags import resident_engineer_enabled
from app.workbench.flags import build_mode_enabled, kimi_workbench_enabled
from app.workbench.live_audit import AuditRejected, validate_audit_artifact


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    """Ensure the test environment does not accidentally enable gated kernels."""
    for name in (
        "RESIDENT_ENGINEER_ENABLED",
        "BUILD_MODE_ENABLED",
        "KIMI_WORKBENCH_ENABLED",
        "CHANGE_REQUEST_INTAKE_ENABLED",
        "RESIDENT_EMIT_CHANGE_REQUESTS",
    ):
        monkeypatch.delenv(name, raising=False)


def _add_path(prefixes: set, path: str) -> None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2:
        prefixes.add(f"/{parts[0]}/{parts[1]}")
    elif parts and parts[0]:
        prefixes.add(f"/{parts[0]}")


def _route_prefixes(app) -> set:
    """Collect the unique path prefixes mounted on the FastAPI app.

    Newer FastAPI represents ``include_router`` results as lazy
    ``_IncludedRouter`` objects without a ``path`` attribute — the mounted
    prefix lives on ``include_context`` and the concrete routes on
    ``original_router``. Read both shapes so the invariant checks the app,
    not one FastAPI version's internals.
    """
    prefixes = set()
    for route in app.routes:
        ctx = getattr(route, "include_context", None)
        if ctx is not None:
            base = (getattr(ctx, "prefix", "") or "").rstrip("/")
            inner = getattr(route, "original_router", None)
            inner_routes = getattr(inner, "routes", []) if inner else []
            if base:
                _add_path(prefixes, base)
            for sub in inner_routes:
                sub_path = getattr(sub, "path", "") or ""
                _add_path(prefixes, f"{base}{sub_path}")
            continue
        path = getattr(route, "path", "")
        if path:
            _add_path(prefixes, path)
    return prefixes


REQUIRED_PREFIXES = {
    "/v1/auth",
    "/v1/billing",
    "/v1/sessions",
    "/v1/domains",
    "/v1/factory",
    "/v1/resident",
    "/v1/workbench",
    "/v1/change-requests",
    "/health",
    "/ready",
    "/version",
}


def test_all_documented_routers_are_mounted():
    prefixes = _route_prefixes(app.main.app)
    missing = REQUIRED_PREFIXES - prefixes
    assert not missing, f"Routers missing from app: {missing}"


def test_feature_flagged_kernels_default_off():
    """Default OFF is a house security pattern; status endpoints must be honest."""
    assert resident_engineer_enabled() is False
    assert build_mode_enabled() is False
    assert kimi_workbench_enabled() is False
    assert change_request_intake_enabled() is False


def test_feature_flagged_status_endpoints_are_honest(client):
    """Status endpoints report disabled when flags are off — no fake readiness."""
    resp = client.get("/v1/resident/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["maturity"] == "APPRENTICE"
    assert "allowlisted_heal_actions" in body

    resp = client.get("/v1/workbench/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["build_mode_enabled"] is False
    assert body["kimi_workbench_enabled"] is False
    assert body["deploy_credentials"] is False

    resp = client.get("/v1/change-requests/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intake_enabled"] is False
    assert body["execution"] is False
    assert "M3 paperwork only" in body["honesty"]


def test_gated_mutation_endpoints_refuse_service_when_disabled(client):
    """When flags are off, mutation endpoints must fail closed, not silently stub."""
    # Resident engineer observe requires the flag.
    resp = client.get("/v1/resident/observe")
    assert resp.status_code == 503, resp.text

    # Workbench run requires the flag.
    resp = client.post("/v1/workbench/run", json={"request_id": "cr-123"})
    assert resp.status_code == 503, resp.text

    # Change-request queue requires the flag.
    resp = client.get("/v1/change-requests/queue")
    assert resp.status_code == 503, resp.text


def test_platform_chat_flow_exports():
    """Platform chat flow exposes the documented seam functions."""
    for name in (
        "draft_from_chat",
        "approve_and_generate",
        "has_pending_blueprint",
        "apply_refinement",
        "platform_chat_enabled",
    ):
        fn = getattr(platform_chat_flow, name, None)
        assert callable(fn), f"{name} is not callable"


def test_planner_fail_closed_real_api():
    """Bogus block ids are NOT planned as REUSE/ADAPT/COMPOSE.

    Mirrors gates.py's dual_registry_gate logic: call the real planner and
    accept either a fail-closed DualRegistryError or a non-REUSE strategy.
    """
    blueprint = ProductBlueprint.model_validate(
        {
            "schema_version": "product_blueprint.v1",
            "product_id": "bogus",
            "product_name": "Bogus",
            "vertical": "bogus",
            "summary": "test",
            "factory_scenario": "CREATE_PRODUCT",
            "capabilities": [
                {
                    "id": "ghost_capability",
                    "description": "uses a block that does not exist",
                    "block_ids": ["no_such_block_xyz"],
                    "strategy_hint": "REUSE",
                }
            ],
            "ui_modules": [],
            "connectors": [],
            "edge_profile": "standard",
            "human_authority": True,
        }
    )
    try:
        plan = CapabilityPlanner(None, None).plan(blueprint)
    except DualRegistryError:
        # Fail-closed is the expected safe behavior.
        return
    ghost = [c for c in plan.capabilities if c.capability_id == "ghost_capability"]
    if ghost:
        assert ghost[0].strategy not in {"REUSE", "ADAPT", "COMPOSE"}, (
            f"ghost capability planned as {ghost[0].strategy} with bogus block ids"
        )
    # absent from the plan is also an acceptable fail-closed disposition


def test_suite_hygiene_no_skip_without_reason():
    """Every skip/xfail in backend/tests must carry a reason."""
    offenders = []
    tests_dir = Path("tests")
    for pyfile in tests_dir.rglob("*.py"):
        source = pyfile.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name not in ("skip", "skipif", "xfail"):
                continue
            if not any(kw.arg == "reason" for kw in node.keywords):
                offenders.append(f"{pyfile.relative_to(tests_dir)}:{node.lineno}")

    assert not offenders, f"skip/xfail missing reason= in backend/tests: {offenders}"


def test_live_audit_validator(tmp_path):
    """Audit artifact validator rejects missing, dead, stale and accepts fresh LIVE."""
    # missing file
    with pytest.raises(AuditRejected, match="not found"):
        validate_audit_artifact(tmp_path / "missing.json")

    # dead check
    dead_path = tmp_path / "dead.json"
    dead_path.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "checks": [{"name": "x", "status": "DEAD"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AuditRejected, match="DEAD checks"):
        validate_audit_artifact(dead_path)

    # stale (25h old)
    stale_path = tmp_path / "stale.json"
    stale_time = datetime.now(timezone.utc) - timedelta(hours=25)
    stale_path.write_text(
        json.dumps(
            {
                "ran_at": stale_time.isoformat(),
                "checks": [{"name": "x", "status": "LIVE"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AuditRejected, match="stale"):
        validate_audit_artifact(stale_path)

    # fresh all-LIVE
    fresh_path = tmp_path / "fresh.json"
    fresh_time = datetime.now(timezone.utc) - timedelta(hours=1)
    fresh_path.write_text(
        json.dumps(
            {
                "ran_at": fresh_time.isoformat(),
                "checks": [{"name": "x", "status": "LIVE"}],
            }
        ),
        encoding="utf-8",
    )
    data = validate_audit_artifact(fresh_path)
    assert data["checks"][0]["status"] == "LIVE"
