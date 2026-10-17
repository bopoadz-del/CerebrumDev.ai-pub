"""F0: the model writes the brief's NARRATIVE; code owns the CONTRACT.

The CONTRACT is the compiled brief exactly as the compiler writes it today and
reaches the coder verbatim after the narrative. A narrative whose shape is
wrong is regenerated once, then dropped for the CONTRACT-only brief -- and
every call is in the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import architect
from app.factory.build.architect import (
    NARRATIVE_SEPARATOR,
    attach_narrative,
    compose_narrative,
    dispatch_text,
)
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.writer_phases import WRITER_PHASE_BACKEND, compile_phase_brief
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(scope="module")
def compiled():
    bp = load_blueprint(SMOKE)
    return compile_brief(bp, plan_blueprint(bp))


@pytest.fixture(scope="module")
def names(compiled):
    resolved = sorted(architect._resolved_blocks(compiled))
    foreign = next(s for s in compiled.store_ids if s not in resolved)
    return {"cap": compiled.capabilities[0], "block": resolved[0], "foreign_block": foreign}


class _Llm:
    """A stub architect model: replies in order, records what it was asked."""

    def __init__(self, *replies: Any):
        self.replies = list(replies)
        self.calls: List[List[Dict[str, str]]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        return reply if isinstance(reply, dict) else {"narrative": reply}


@dataclass
class _Ctx:
    state: Dict[str, Any] = field(default_factory=lambda: {"brief": "a platform for the team"})
    notes: List[Dict[str, Any]] = field(default_factory=list)

    def note(self, detail, **payload):
        self.notes.append({"detail": detail, **payload})


def _good_text(n):
    return (
        f"This platform serves its operators. {n['cap']} reads through "
        f"{n['block']}; build the handler first, then its view."
    )


def _good(n):
    return {"narrative": _good_text(n), "blocks": [n["block"]], "capabilities": [n["cap"]]}


def test_good_narrative_goes_first_and_the_contract_follows_byte_identical(compiled, names):
    contract_before = compiled.text
    ctx = _Ctx()
    out = attach_narrative(ctx, compiled, llm=_Llm(_good(names)), mode="on")
    assert out.text == contract_before  # the CONTRACT field is never touched
    sent = dispatch_text(out)
    assert sent == out.narrative + NARRATIVE_SEPARATOR + contract_before
    assert sent.endswith(contract_before)  # appended verbatim, nothing omitted
    assert lint_brief(out, known_literals=frozenset()).ok  # contract lint unchanged
    [call] = [n for n in ctx.notes if n.get("stage") == "architect"]
    rec = call["architect"]
    assert rec["lint"]["ok"] and not rec["fallback"] and rec["attempt"] == 1
    assert rec["inputs_hash"] and rec["output_hash"] and rec["entry"] == "narrative"


def test_contract_is_identical_with_the_flag_off_shadow_and_on(compiled, names, monkeypatch):
    bp = load_blueprint(SMOKE)
    texts = []
    for mode in ("off", "shadow", "on"):
        monkeypatch.setenv(architect.FLAG_ENV, mode)
        fresh = compile_brief(bp, plan_blueprint(bp))
        texts.append(attach_narrative(_Ctx(), fresh, llm=_Llm(_good(names))).text)
    assert texts[0] == texts[1] == texts[2]


def test_unresolvable_block_is_regenerated_once_then_falls_back_to_contract_only(compiled, names):
    bad = {"narrative": "Compose it from the ledger block.", "blocks": [names["foreign_block"]]}
    llm = _Llm(bad, bad)
    ctx = _Ctx()
    out = attach_narrative(ctx, compiled, llm=llm, mode="on")
    assert len(llm.calls) == 2  # exactly one regeneration
    assert "refused by the lint" in llm.calls[1][-1]["content"]  # findings fed back
    assert out.narrative == "" and dispatch_text(out) == compiled.text  # CONTRACT-only
    recs = [n["architect"] for n in ctx.notes if n.get("stage") == "architect"]
    assert [r["attempt"] for r in recs] == [1, 2]
    assert recs[-1]["fallback"] is True and not recs[-1]["lint"]["ok"]
    assert ctx.state["brief_narrative"]["fallback"] is True


def test_regeneration_that_fixes_the_defect_is_used(compiled, names):
    bad = {"narrative": "Use the ledger block.", "blocks": [names["foreign_block"]]}
    out = attach_narrative(_Ctx(), compiled, llm=_Llm(bad, _good(names)), mode="on")
    assert out.narrative == _good_text(names)


def test_a_bare_store_block_id_this_build_did_not_resolve_is_refused(compiled, names):
    res = compose_narrative(
        compiled,
        llm=_Llm(f"Lean on {names['foreign_block']} for this.", f"Lean on {names['foreign_block']}."),
        mode="on",
        known_literals=frozenset(),
    )
    assert res.fallback and "does not resolve" in res.lint["errors"][0]


def test_capability_outside_the_blueprint_is_refused(compiled, names):
    bad = {"narrative": "Also ship a ledger sync.", "capabilities": ["zorblat_ledger_sync"]}
    res = compose_narrative(compiled, llm=_Llm(bad, bad), mode="on", known_literals=frozenset())
    assert res.fallback
    assert any("outside the blueprint" in e for e in res.lint["errors"])


def test_check_tags_session_ids_and_slots_are_refused(compiled, names):
    for bad in (
        _good_text(names) + " [check:zorblat_check]",
        _good_text(names) + " see sess_0123456789ab",
        _good_text(names) + " {{TARGET}}",
    ):
        res = compose_narrative(compiled, llm=_Llm(bad, bad), mode="on", known_literals=frozenset())
        assert res.fallback, bad


def test_length_cap(compiled, names, monkeypatch):
    monkeypatch.setenv(architect.MAX_CHARS_ENV, "40")
    res = compose_narrative(compiled, llm=_Llm(_good(names), _good(names)), mode="on", known_literals=frozenset())
    assert res.fallback and any("cap" in e for e in res.lint["errors"])


def test_off_makes_no_call_and_shadow_records_but_dispatches_the_contract(compiled, names):
    llm = _Llm(_good(names))
    ctx = _Ctx()
    assert attach_narrative(ctx, compiled, llm=llm, mode="off") is compiled
    assert llm.calls == [] and ctx.notes == []

    ctx = _Ctx()
    out = attach_narrative(ctx, compiled, llm=_Llm(_good(names)), mode="shadow")
    assert out.narrative == "" and dispatch_text(out) == compiled.text
    [rec] = [n["architect"] for n in ctx.notes if n.get("stage") == "architect"]
    assert rec["mode"] == "shadow" and rec["lint"]["ok"] and rec["output"] == _good_text(names)


def test_a_model_failure_is_a_fallback_not_a_crash(compiled):
    def boom(_messages):
        raise RuntimeError("provider down")

    out = attach_narrative(_Ctx(), compiled, llm=boom, mode="on")
    assert out.narrative == "" and dispatch_text(out) == compiled.text


def test_every_phase_brief_carries_the_narrative_then_its_contract(compiled, names):
    out = attach_narrative(_Ctx(), compiled, llm=_Llm(_good(names)), mode="on")
    phase = compile_phase_brief(out, WRITER_PHASE_BACKEND)
    sent = dispatch_text(phase)
    assert sent.startswith(out.narrative + NARRATIVE_SEPARATOR)
    assert sent.endswith(compiled.text)
