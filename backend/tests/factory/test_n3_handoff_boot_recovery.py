"""A restart during the Store-gate wait must not strand a passing platform.

Audit 2026-09-19: 8 of 31 cerebrum-builds branches were green in Docker
while the Factory still said "building / HANDOFF_TO_N3". The verdict is
collected by a daemon thread; a deploy during the ~5-10 min CI wait killed
it and nothing restarted it.
"""

from __future__ import annotations

from app.factory.build import n3_store_gate, orphan_recovery
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind


def _workspace(root, session, product, *, handed_off=True, green=False):
    out = root / "sessions" / session / product
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product, inputs_hash="abc")
    if handed_off:
        ledger.append(
            EventKind.NOTE, role=BuildRole.STORE_MANAGER, detail="handed off",
            payload={"honesty": n3_store_gate.HANDOFF_TO_N3, "handoff": n3_store_gate.HANDOFF_TO_N3},
        )
    if green:
        ledger.append(
            EventKind.NOTE, role=BuildRole.STORE_MANAGER, detail="13/13",
            payload={"honesty": n3_store_gate.N3_STORE_GATE_GREEN},
        )
    return out


def test_boot_restarts_the_waiter_for_every_build_still_awaiting_its_gate(tmp_path, monkeypatch):
    waiting_a = _workspace(tmp_path, "sess_aaaaaaaaaaaaaaaa", "bakery")
    waiting_b = _workspace(tmp_path, "sess_bbbbbbbbbbbbbbbb", "fleetops")
    _workspace(tmp_path, "sess_cccccccccccccccc", "vet", green=True)
    _workspace(tmp_path, "sess_dddddddddddddddd", "hotel", handed_off=False)
    started = []
    monkeypatch.setattr(
        n3_store_gate, "start_n3_ingest_job",
        lambda out, **kw: started.append((str(out), kw.get("session_id"))) or True,
    )

    results = orphan_recovery.recover_stranded_n3_handoffs(outputs_root=tmp_path)

    assert sorted(s for s, _ in started) == sorted([str(waiting_a), str(waiting_b)])
    assert dict(started)[str(waiting_a)] == "sess_aaaaaaaaaaaaaaaa"
    assert len(results) == 2


def test_a_live_waiter_is_not_doubled(tmp_path, monkeypatch):
    _workspace(tmp_path, "sess_aaaaaaaaaaaaaaaa", "bakery")
    monkeypatch.setattr(n3_store_gate, "n3_ingest_live", lambda out: True)
    started = []
    monkeypatch.setattr(n3_store_gate, "start_n3_ingest_job", lambda *a, **k: started.append(a) or True)

    assert orphan_recovery.recover_stranded_n3_handoffs(outputs_root=tmp_path) == []
    assert started == []


def test_boot_calls_it():
    import inspect

    from app import main

    assert "recover_stranded_n3_handoffs" in inspect.getsource(main._lifespan)
