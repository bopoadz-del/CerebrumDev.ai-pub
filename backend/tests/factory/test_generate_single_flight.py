"""H3: generate is single-flight; a second start while building is refused."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build_jobs import start_runner_build
from app.factory.blueprint import CapabilitySpec, ProductBlueprint


def _bp(product_id: str = "single-flight-demo") -> ProductBlueprint:
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id=product_id,
        product_name="Single Flight",
        vertical="demo",
        summary="single flight",
        capabilities=[
            CapabilitySpec(
                id="audit",
                description="audit",
                block_ids=["audit"],
                strategy_hint="REUSE",
            )
        ],
    )


def test_start_runner_build_refuses_second_start(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "runner")
    out = tmp_path / "product"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="single-flight-demo", inputs_hash="abc123")
    ledger.append(EventKind.PHASE_STARTED, role="COLLECTOR", detail="in flight")

    started = []

    def _no_thread(*_a, **_k):
        started.append(1)
        raise AssertionError("must not spawn a second runner thread")

    monkeypatch.setattr("threading.Thread.start", _no_thread)
    result = start_runner_build(_bp(), out)
    assert result["already_running"] is True
    assert result["build"]["state"] == "building"
    assert started == []


def test_http_generate_409_when_already_building(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.session_store import create_session, get_session, update_session
    from app.main import app

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "runner")
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)

    session_id = "sess_single_flight"
    create_session(session_id, "tester")
    state = get_session(session_id)
    assert state is not None
    state.product_design.blueprint = _bp().model_dump(mode="json")
    state.product_design.blueprint_approved = True
    out = Path(tmp_path / "factory_outputs" / "sessions" / session_id / "single-flight-demo")
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="single-flight-demo", inputs_hash="abc123")
    ledger.append(EventKind.PHASE_STARTED, role="WRITER", detail="in flight")
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "single-flight-demo",
        "engine": "runner",
        "build": {"state": "building"},
    }
    update_session(session_id, state)

    client = TestClient(app)
    res = client.post(f"/v1/sessions/{session_id}/product/generate", json={})
    assert res.status_code == 409, res.text
    assert "already" in res.json()["detail"].lower()
