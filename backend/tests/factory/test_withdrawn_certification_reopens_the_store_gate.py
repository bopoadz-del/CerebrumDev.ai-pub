"""A certification the Store gate withdrew makes the run a failed platform.

Live: the cycle-2 smoke B build was certified on a false 22/22 and revoked by
cerebrum-builds#41. Its ledger still read "succeeded": the export refused it
(certification withdrawn) while Continue answered "already complete", so the
platform could never be re-gated or sent back to the writer. Recorded as the
Store gate's failed verdict, the Store phase is no longer passed and Continue
resumes the platform there by the normal failed-platform path.
"""

from __future__ import annotations

from app.factory.build import n3_store_gate
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.ledger import BuildLedger, EventKind


def _succeeded(root):
    ledger = BuildLedger(root / "build_ledger.jsonl")
    ledger.start_run(product_id="zorblat_platform", inputs_hash="h0")
    for role in BUILD_PHASES:
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail=f"{role.value} ok")
    ledger.append(EventKind.RUN_SUCCEEDED, role=BUILD_PHASES[-1], detail="certified",
                  payload={"pilot_ready": True})
    return ledger


def test_a_withdrawn_certification_fails_the_store_phase(tmp_path):
    ledger = _succeeded(tmp_path)
    assert n3_store_gate.record_certification_withdrawn(tmp_path, "revoked: false 22/22")
    assert not ledger.succeeded()
    assert ledger.terminal_event().kind is EventKind.RUN_FAILED
    assert BuildRole.STORE_MANAGER not in ledger.completed_roles()
    assert ledger.resume_point() is BuildRole.STORE_MANAGER
    assert n3_store_gate.N3_CERTIFICATION_WITHDRAWN in ledger.terminal_event().detail


def test_it_is_recorded_once(tmp_path):
    _succeeded(tmp_path)
    assert n3_store_gate.record_certification_withdrawn(tmp_path, "revoked")
    assert not n3_store_gate.record_certification_withdrawn(tmp_path, "revoked")


def test_a_run_that_never_succeeded_is_not_touched(tmp_path):
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="zorblat_platform", inputs_hash="h0")
    before = len(ledger.events())
    assert not n3_store_gate.record_certification_withdrawn(tmp_path, "revoked")
    assert len(ledger.events()) == before


def test_continue_reads_a_withdrawn_certification_as_a_failed_platform(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.factory import platform_chat_flow

    _succeeded(tmp_path)
    state = SimpleNamespace(product_design=SimpleNamespace(generation={"output_dir": str(tmp_path)}))
    monkeypatch.setattr(platform_chat_flow, "_generation_output_dir", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(n3_store_gate, "certification_withdrawn", lambda _out, **_k: "store-gate failure: 21/22")
    assert platform_chat_flow.record_withdrawn_certification(state) == "store-gate failure: 21/22"
    assert platform_chat_flow._ledger_for(tmp_path).terminal_event().kind is EventKind.RUN_FAILED


def test_no_verdict_from_github_changes_nothing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.factory import platform_chat_flow

    ledger = _succeeded(tmp_path)
    state = SimpleNamespace(product_design=SimpleNamespace(generation={"output_dir": str(tmp_path)}))
    monkeypatch.setattr(platform_chat_flow, "_generation_output_dir", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(n3_store_gate, "certification_withdrawn", lambda _out, **_k: None)
    assert platform_chat_flow.record_withdrawn_certification(state) is None
    assert ledger.succeeded()
