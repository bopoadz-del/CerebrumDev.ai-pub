"""End-to-end demo flows for CerebrumDev.ai — the stranger, encoded.

  D2  health 200
  D3  drive the "generate a platform" flow: brief -> blueprint -> export
      artifact exists and is shaped like a runnable platform (action routes +
      console UI), and the export ships its own smoke tests.
  D4  an unauthenticated request to a protected route is rejected (401).

D3 runs fully offline: when no architect LLM credit is available the factory
falls back to deterministic drafting, and generation is deterministic block
composition (no LLM) by design.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402


# ── D2: health ──────────────────────────────────────────────────────────────
def test_d2_health():
    with TestClient(app) as c:
        h = c.get("/health")
        assert h.status_code == 200
        r = c.get("/ready")
        assert r.status_code in (200, 503)
        assert "status" in r.json()


# ── D3: generate a platform -> runnable artifact ────────────────────────────
def test_d3_generate_platform_artifact(tmp_path, monkeypatch):
    """The TEMPLATE path's artifact shape (universal console, action routes).

    Pinned to that engine: production now defaults to the role runner, whose
    artifact is asserted in test_d3_runner_is_the_production_artifact below.
    The console and /v1/actions/ routes are template-only output, registered
    in KNOWN_INCOMPLETE 1b as not yet ported.
    """
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
    from app.factory.product_architect import draft_blueprint_from_brief, generate_product

    blueprint = draft_blueprint_from_brief("Build a warehouse operations platform")
    assert blueprint is not None
    # Deterministic drafting must be disclosed, never masqueraded as an LLM draft.
    assert getattr(blueprint, "drafting_mode", None) in {
        "architect_llm",
        "keyword_fallback",
        "golden",
    }

    out = tmp_path / "warehouse-export"
    result = generate_product(blueprint, out)
    assert isinstance(result, dict)

    # The export is shaped like a runnable platform, not a skeleton.
    main_py = out / "app" / "main.py"
    console = out / "app" / "static" / "console.html"
    assert main_py.is_file(), "generated export missing app/main.py"
    assert console.is_file(), "generated export missing the universal console UI"

    main_src = main_py.read_text(encoding="utf-8")
    assert "/v1/actions/" in main_src, "export has no action routes (hollow skeleton)"
    assert 'include_in_schema=False' in main_src or '@app.get("/"' in main_src, "export does not serve the console at /"

    # The export ships its own smoke suite that proves reachability.
    smoke = list(out.rglob("test_*smoke*.py")) + list(out.rglob("test_actions*.py"))
    assert smoke, "generated export ships no smoke tests"


# ── D4: protected route rejects unauthenticated caller ──────────────────────
def test_d4_protected_route_rejects_unauthenticated(monkeypatch):
    monkeypatch.setenv("RESIDENT_ENGINEER_ENABLED", "1")
    with TestClient(app) as c:
        # /v1/resident/heal executes repair actions — must reject without auth.
        resp = c.post("/v1/resident/heal", json={"action_id": "noop"})
        assert resp.status_code == 401, resp.text
        # The public status route still answers.
        assert c.get("/v1/resident/status").status_code == 200

# ── D3b: the PRODUCTION default -> a platform that runs standalone ──────────
def test_d3_runner_is_the_production_artifact(tmp_path, monkeypatch, stub_coder):
    """What a customer downloads today, end to end through the real door.

    Deliberately does NOT set FACTORY_BUILD_ENGINE: this must exercise
    whatever production defaults to, so the day the default changes this test
    changes with it. 0.5: a keyless deterministic writer refuses
    (writer_no_output), so the production door runs a stubbed coding agent --
    still no key, still no network.
    """
    import time

    from app.factory.build_jobs import build_status
    from app.factory.product_architect import draft_blueprint_from_brief, generate_product

    blueprint = draft_blueprint_from_brief("Build a warehouse operations platform")
    out = tmp_path / "warehouse-runner"
    result = generate_product(blueprint, out)
    assert isinstance(result, dict)

    # The build is a background job; wait for its verdict off the ledger.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = build_status(out)
        if status["state"] in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    else:
        raise AssertionError(f"build never finished: {build_status(out)}")
    assert status["state"] == "succeeded", status

    # Shaped like a platform that runs on its own, not a parts list.
    for required in ("app/main.py", "app/routes.py", "app/store.py", "app/jobs.py",
                     "app/dispatch.py", "blocks.lock.json", "tests/test_smoke.py",
                     "scripts/release_gate.py"):
        assert (out / required).is_file(), f"missing {required}"
    assert (out / "vendor" / "blocks").is_dir(), "missing vendor/blocks"
    kit_manifests = list((out / "kits").glob("*/manifest.json"))
    assert kit_manifests, "missing kits/*/manifest.json — export has no kit packs"

    # The failure the physical download exposed: capability handlers that
    # call the operator's store over HTTP instead of running blocks locally.
    for handler in (out / "app" / "actions").glob("*.py"):
        src = handler.read_text(encoding="utf-8")
        assert "httpx" not in src and "/v1/execute" not in src, handler.name
