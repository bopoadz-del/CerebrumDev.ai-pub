"""The coder loop is wall-clock bounded per generation.

New-shape tests for the PRR fix: generation runs in-request on a single
worker, and each GENERATE capability is up to two sequential 120 s LLM calls.
FACTORY_CODER_BUDGET_S bounds the whole loop; capabilities past the deadline
ship the honest stub with the reason recorded, exactly like any other coder
failure — never a fabricated success, never a silent stub. The production
default is stage 1 (~30 min); leftover 300s remaps to that stage, not 2h.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.factory import coder
from app.factory.generator import ProductGenerator


def _bare_generator(cap_ids):
    """A ProductGenerator with just enough state to drive _write_actions."""
    gen = ProductGenerator.__new__(ProductGenerator)
    gen._coder_report = {"written": [], "stubbed": {}}
    gen.blueprint = SimpleNamespace(
        vertical="testing",
        capabilities=[SimpleNamespace(id=cid) for cid in cap_ids],
    )
    gen.plan = SimpleNamespace(
        capabilities=[
            SimpleNamespace(
                capability_id=cid, strategy="GENERATE", block_ids=[], notes=""
            )
            for cid in cap_ids
        ]
    )
    return gen


def test_budget_exhaustion_stubs_remaining_capabilities(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "1")

    # Deterministic clock: the deadline is computed at t=0, every later check
    # sees t=1000 — the budget is spent before the first capability.
    from app.factory import generator as generator_mod

    ticks = iter([0.0] + [1000.0] * 10)
    monkeypatch.setattr(
        generator_mod.time, "monotonic", lambda: next(ticks, 1000.0)
    )

    calls = []
    monkeypatch.setattr(
        coder, "generate_handler_body", lambda *a, **k: calls.append(a) or "return {}"
    )

    gen = _bare_generator(["cap-a", "cap-b"])
    gen._write_actions(tmp_path)

    assert calls == [], "no LLM call may start after the budget is spent"
    assert set(gen._coder_report["stubbed"]) == {"cap-a", "cap-b"}
    for reason in gen._coder_report["stubbed"].values():
        assert "budget exhausted" in reason
        assert "FACTORY_CODER_BUDGET_S" in reason
    # The honest stub modules still shipped — the product is complete, labeled.
    assert (tmp_path / "app" / "actions" / "cap_a.py").exists()
    assert (tmp_path / "app" / "actions" / "cap_b.py").exists()


def test_zero_budget_disables_the_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "0")

    calls = []

    def _always_called(*a, **k):
        calls.append(a)
        raise coder.CoderError("forced failure so the stub path runs")

    monkeypatch.setattr(coder, "generate_handler_body", _always_called)

    gen = _bare_generator(["cap-a", "cap-b"])
    gen._write_actions(tmp_path)

    assert len(calls) == 2, "budget 0 must not gate any capability"
    for reason in gen._coder_report["stubbed"].values():
        assert "budget" not in reason


def test_budget_default_and_parse_failure(monkeypatch):
    monkeypatch.delenv("FACTORY_CODER_BUDGET_S", raising=False)
    assert coder.coder_budget_s() == coder.DEFAULT_CODER_BUDGET_S
    assert coder.DEFAULT_CODER_BUDGET_S == 1800.0
    assert coder.DEFAULT_CODER_BUDGET_S < 7200.0
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "not-a-number")
    assert coder.coder_budget_s() == coder.DEFAULT_CODER_BUDGET_S


def test_legacy_300s_budget_does_not_stub_a_pilot(monkeypatch):
    """Live leftover FACTORY_CODER_BUDGET_S=300 stubbed remaining
    capabilities after five minutes. Remap to stage 1, not a silent 2h.
    """
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "300")
    assert coder.coder_budget_s() == coder.DEFAULT_CODER_BUDGET_S
    assert coder.coder_budget_s() == 1800.0
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "1")
    assert coder.coder_budget_s() == 1.0
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "0")
    assert coder.coder_budget_s() == 0.0
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "7200")
    assert coder.coder_budget_s() == 7200.0
    monkeypatch.setenv("FACTORY_CODER_BUDGET_S", "9000")
    assert coder.coder_budget_s() == 9000.0
