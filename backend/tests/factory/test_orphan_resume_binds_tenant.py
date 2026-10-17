"""A build resumed after a restart runs under the tenant that started it.

Live 2026-10-07 22:04Z (5dd46d47): Fargate retired the backend task while
the vineyard build sess_d023c15b78a041cd was in WRITER. Boot recovery found
the orphan and resumed it -- with no tenant. At boot there is no request and
no principal, and the resume called ``start_runner_build(blueprint, out)``
with no identity, so ``bind_tenant_store(None)`` returned None and the
worker refused: "WRITER failed: codewhale_worker_failed:
no_authenticated_tenant". The build died as a WRITER failure it never had.

The tenant is durable: the session state the Floor started the build from
records the account (``user_id``) -- the same identity every Floor start
passes as ``tenant_identity``. A resume binds THAT account. When no account
is recorded, the resume stops with a typed reason the owner can act on
(press Continue), never a WRITER failure.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from app.factory.blueprint import CapabilitySpec, ProductBlueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.orphan_recovery import (
    ORPHAN_NO_TENANT_REASON,
    recover_orphaned_model_calls,
)
from app.factory.build.runner import blueprint_hash
from app.factory.build_jobs import build_status

SESSION_ID = "sess_d023c15b78a041cd"
ACCOUNT = "acct_vineyard_owner"


def _bp() -> ProductBlueprint:
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="winery-ops",
        product_name="Winery Ops",
        vertical="winery",
        summary="fermentation, barrels, harvest",
        capabilities=[
            CapabilitySpec(
                id="tank_log",
                description="tanks",
                block_ids=["storage"],
                strategy_hint="REUSE",
            )
        ],
    )


def _writer_inflight(out: Path, inputs_hash: str) -> None:
    """COLLECTOR+CLONER done, WRITER model call open, no live worker."""
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="winery-ops", inputs_hash=inputs_hash)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail=role.value)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": 7230.0,
            "done": 0,
            "total": 1,
        },
    )


def _plant(tmp_path: Path, monkeypatch, state: dict) -> Path:
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready",
        lambda: None,
    )
    bp = _bp()
    out = tmp_path / "factory_outputs" / "sessions" / SESSION_ID / "winery-ops"
    _writer_inflight(out, blueprint_hash(bp))
    state_dir = tmp_path / "storage" / "sessions" / SESSION_ID
    state_dir.mkdir(parents=True)
    body = {
        "version": 2,
        "session_id": SESSION_ID,
        "product_design": {"blueprint": bp.model_dump(mode="json")},
    }
    body.update(state)
    state_dir.joinpath("state.json").write_text(json.dumps(body), encoding="utf-8")
    return out


def test_restart_resume_binds_the_tenant_that_started_the_build(tmp_path, monkeypatch):
    _plant(tmp_path, monkeypatch, {"user_id": ACCOUNT})

    held = threading.Event()
    bound = []

    def _hold(blueprint, output_dir, blocks_root, cycle, tenant_store, *_a, **_k):
        bound.append(tenant_store)
        held.wait(timeout=5)

    monkeypatch.setattr("app.factory.build_jobs._run", _hold)
    try:
        results = recover_orphaned_model_calls(
            outputs_root=tmp_path / "factory_outputs",
            storage_root=tmp_path / "storage",
        )
        assert [r["action"] for r in results] == ["resumed"], results
        assert len(bound) == 1
        store = bound[0]
        # The worker refuses an unbound job; the resume must carry a handle.
        assert store is not None, "resume ran with no tenant (no_authenticated_tenant)"
        expected = hashlib.sha256(ACCOUNT.encode("utf-8")).hexdigest()[:32]
        assert store.tenant_key == expected
    finally:
        held.set()


def test_resume_without_a_recorded_tenant_stops_with_a_typed_reason(tmp_path, monkeypatch):
    out = _plant(tmp_path, monkeypatch, {})

    started = []
    monkeypatch.setattr(
        "app.factory.build_jobs._run", lambda *a, **k: started.append(1)
    )
    results = recover_orphaned_model_calls(
        outputs_root=tmp_path / "factory_outputs",
        storage_root=tmp_path / "storage",
    )
    assert started == [], "an unbound build must never start"
    assert len(results) == 1 and results[0]["action"] == "failed", results
    assert results[0]["reason"] == ORPHAN_NO_TENANT_REASON
    status = build_status(out)
    assert status["state"] == "failed", status
    # Owner-visible and actionable -- and not dressed up as a WRITER failure.
    assert "Continue" in status["detail"]
    assert "WRITER failed" not in status["detail"]
    terminal = BuildLedger(out / "build_ledger.jsonl").terminal_event()
    assert terminal.payload.get("reason") == ORPHAN_NO_TENANT_REASON
    assert terminal.payload.get("next") == "continue"
