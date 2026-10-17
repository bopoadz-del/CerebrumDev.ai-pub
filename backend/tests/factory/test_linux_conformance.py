"""Linux-provable S9 conformance: fresh RoleRunner vs LotDesk-as-shipped.

Windows/cp1252 is not claimed here. This host is Linux/UTF-8.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.lotdesk_gate import inspect_path, reject_lotdesk_as_shipped
from app.factory.build.roles import _DISPATCH_RUNTIME, _render_dockerfile
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LOTDESK_ZIP = (
    ROOT / "backend" / "tests" / "factory" / "fixtures" / "lotdesk_pilot_ready.zip"
)


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_emitted_dispatch_has_no_fabrication_helpers():
    assert "_default_block_field" not in _DISPATCH_RUNTIME
    assert "_ALWAYS_FILL" not in _DISPATCH_RUNTIME
    assert "_ensure_offline_block_input" not in _DISPATCH_RUNTIME
    assert "DispatchContractError" in _DISPATCH_RUNTIME


def test_emitted_dockerfile_runs_release_gate():
    text = _render_dockerfile()
    assert "scripts/release_gate.py" in text
    assert "requirements-dev.txt" in text


def test_fresh_role_runner_tree_is_clean_of_lotdesk_f18_and_f19(tmp_path, stub_coder):
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()

    dispatch = (out / "app" / "dispatch.py").read_text(encoding="utf-8")
    assert "_default_block_field" not in dispatch
    assert "_ALWAYS_FILL" not in dispatch

    dockerfile = (out / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/release_gate.py" in dockerfile

    findings = inspect_path(out)
    codes = {item.code for item in findings}
    assert "F18" not in codes, findings
    assert "F19" not in codes, findings

    lotdesk = reject_lotdesk_as_shipped(LOTDESK_ZIP)
    assert "F18" in lotdesk["codes"]
    assert "F19" in lotdesk["codes"]


def test_templated_route_does_not_pass_on_kernel_refusal():
    """Kernel status != success must not persist or answer ok: True."""
    import asyncio

    from app.factory.build.roles import _templated_route_body

    body = _templated_route_body({"fields": []})
    ns: dict = {}
    # Phase 2 §0.2: the templated body calls run_capability(CAPABILITY_ID,
    # payload, request) — the probe threads a request stub through.
    exec(
        "async def _route(payload, run_capability, save, request=None, CAPABILITY_ID='cap'):\n" + body,
        ns,
    )
    saved = []

    async def refuse(cap, payload, request=None):
        return {"status": "error", "error_message": "block refused"}

    out = asyncio.run(ns["_route"]({"n": 1}, refuse, saved.append))
    assert out["ok"] is False
    assert saved == [], "refusal must not persist"
    assert "block refused" in str(out.get("error"))


def test_templated_route_persists_only_after_kernel_success():
    import asyncio

    from app.factory.build.roles import _templated_route_body

    body = _templated_route_body({"fields": []})
    ns: dict = {}
    # Phase 2 §0.2: the templated body calls run_capability(CAPABILITY_ID,
    # payload, request) — the probe threads a request stub through.
    exec(
        "async def _route(payload, run_capability, save, request=None, CAPABILITY_ID='cap'):\n" + body,
        ns,
    )
    saved = []

    async def ok(cap, payload, request=None):
        return {"status": "success", "output": {"n": 1}}

    out = asyncio.run(ns["_route"]({"n": 1}, ok, saved.append))
    assert out["ok"] is True
    assert saved == [{"n": 1}]
