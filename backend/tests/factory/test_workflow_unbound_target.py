"""WRITER gate: a handler may dispatch only to blocks the product has.

A workflow step (or an ``execute`` call) that names a real Store block bound
to no capability in the product fails at runtime as ``Block '<id>' not found``
— today only PRODUCT/TESTER sees it, after the whole build. The gate catches
it statically with an actionable message. These tests monkeypatch the Store
registry so they need no on-disk store fixture; the rule is structural (bound
block_ids + Store registry), never a capability's or a block's name.
"""
from pathlib import Path

import pytest

import app.factory.build.workflow_accept as wa


class _Cap:
    def __init__(self, capability_id, block_ids):
        self.capability_id = capability_id
        self.block_ids = list(block_ids)


def _write_handler(root: Path, cid: str, body: str) -> None:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / f"{cid}.py").write_text(body, encoding="utf-8")


def _patch_store(monkeypatch, store_ids):
    monkeypatch.setattr(
        wa, "block_manifests", lambda *a, **k: {b: {} for b in store_ids}
    )


def test_unbound_event_bus_step_is_flagged(tmp_path, monkeypatch):
    _patch_store(monkeypatch, {"workflow", "transaction_state_machine", "event_bus"})
    inv = [_Cap("work_orders_lifecycle", ["workflow", "transaction_state_machine"])]
    _write_handler(
        tmp_path,
        "work_orders_lifecycle",
        'def handle(payload):\n'
        '    steps = [{"block": "event_bus", "action": "publish",\n'
        '              "input": {"topic": "t"}}]\n'
        '    return execute("workflow", {"steps": steps})\n',
    )
    errs = wa.workflow_step_unbound_target_errors(tmp_path, inv)
    assert any("event_bus" in e and "work_orders_lifecycle" in e for e in errs)


def test_direct_execute_to_unbound_block_is_flagged(tmp_path, monkeypatch):
    _patch_store(monkeypatch, {"workflow", "event_bus"})
    inv = [_Cap("c", ["workflow"])]
    _write_handler(
        tmp_path, "c", 'def handle(p):\n    return execute("event_bus", {"topic": "x"})\n'
    )
    assert wa.workflow_step_unbound_target_errors(tmp_path, inv)


def test_only_bound_blocks_is_clean(tmp_path, monkeypatch):
    _patch_store(monkeypatch, {"workflow", "transaction_state_machine", "event_bus"})
    inv = [_Cap("c", ["workflow", "transaction_state_machine"])]
    _write_handler(
        tmp_path,
        "c",
        'def handle(p):\n'
        '    steps = [{"block": "transaction_state_machine",\n'
        '              "action": "transition"}]\n'
        '    return execute("workflow", {"steps": steps})\n',
    )
    assert wa.workflow_step_unbound_target_errors(tmp_path, inv) == []


def test_dynamic_or_non_store_id_not_flagged(tmp_path, monkeypatch):
    # A runtime-built id (a variable) and a literal that is not a real Store
    # block must never be flagged — the gate flags only what it can read.
    _patch_store(monkeypatch, {"workflow", "event_bus"})
    inv = [_Cap("c", ["workflow"])]
    _write_handler(
        tmp_path,
        "c",
        'def handle(p):\n'
        '    bid = pick()\n'
        '    steps = [{"block": bid}, {"block": "not_a_real_block"}]\n'
        '    return execute("workflow", {"steps": steps})\n',
    )
    assert wa.workflow_step_unbound_target_errors(tmp_path, inv) == []


def test_assert_raises_actionable_halt(tmp_path, monkeypatch):
    _patch_store(monkeypatch, {"workflow", "event_bus"})
    inv = [_Cap("c", ["workflow"])]
    _write_handler(tmp_path, "c", 'def handle(p):\n    return execute("event_bus", {})\n')
    with pytest.raises(wa.EventBusWorkflowHalt) as exc:
        wa.assert_workflow_step_targets_bound(tmp_path, inv)
    assert "event_bus" in str(exc.value)
