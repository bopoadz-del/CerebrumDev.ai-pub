"""A terminal RUN_FAILED / rework-exhausted run must not swallow a new brief.

Live hole: sending the same residential-lettings brief after TESTER-red
rework-exhausted replied "Resuming … Same blueprint hash — not starting
over" and the Floor stayed CODING AGENT STOPPED. Resume of an interrupted
(non-terminal) run must still work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory import platform_chat_flow, platform_chat_llm
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build_jobs import next_fresh_output, start_runner_build
from app.models.session import ProductDesignState, SessionState


def _failed_lettings_state(tmp_path: Path) -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    out = tmp_path / "residential-lettings"
    out.mkdir()
    (out / "app").mkdir()
    (out / "app" / "handler.py").write_text("# torn", encoding="utf-8")

    ledger = BuildLedger(out / "build_ledger.jsonl")
    inputs_hash = "lettings-same-hash"
    ledger.start_run(product_id="residential-lettings", inputs_hash=inputs_hash)
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER, BuildRole.WRITER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.TESTER, detail="pilot red")
    ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="round 1")
    ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="round 2")
    ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="round 3")
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail="rework budget of 3 exhausted; TESTER gate still failing: "
        "PRODUCT (pilot-marked suite): suite is red",
        payload={"cycle": "pilot", "outcome": "FAILED_BUDGET_SPENT", "rework_used": 3},
    )

    s = SessionState(session_id="sess-dead", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    bp = draft_blueprint_from_brief("build a platform for residential lettings")
    s.product_design.blueprint = bp.model_dump(mode="json")
    s.product_design.blueprint_approved = True
    s.product_design.build_level = "pilot"  # the user's typed choice
    s.product_design.generation = {
        "output_dir": str(out),
        "inputs_hash": inputs_hash,
        "product_id": bp.product_id,
        "engine": "runner",
        "build": {
            "state": "failed",
            "phases_done": 3,
            "phases_total": 5,
            "cycle": "pilot",
            "outcome": "FAILED_BUDGET_SPENT",
        },
        "phases_done": 3,
    }
    return s


async def _collect_events(session_id: str, message: str, action=None, value=None):
    from app.factory.floor_actions import parse_action
    from app.routers import chat as chat_router

    events = []
    async for raw in chat_router._stream_response(
        session_id, message, parse_action(action), value
    ):
        lines = [line for line in raw.strip().splitlines() if line]
        ev = {"event": "", "data": ""}
        for line in lines:
            if line.startswith("event:"):
                ev["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                ev["data"] = json.loads(line.split(":", 1)[1].strip())
        events.append(ev)
    return events


def test_failed_run_is_terminal_not_resumable(tmp_path):
    state = _failed_lettings_state(tmp_path)
    assert platform_chat_flow.is_generation_terminal_failure(state)
    assert not platform_chat_flow.is_generation_resumable(state)
    assert not platform_chat_flow.is_generation_complete(state)
    assert not platform_chat_flow.is_pilot_ready(state)


def test_next_fresh_output_skips_a_failed_ledger(tmp_path):
    dead = tmp_path / "residential-lettings"
    dead.mkdir()
    (dead / "build_ledger.jsonl").write_text("{}\n", encoding="utf-8")
    fresh = next_fresh_output(dead)
    assert fresh == tmp_path / "residential-lettings__run2"
    assert not (fresh / "build_ledger.jsonl").is_file()


def test_start_or_resume_coder_after_terminal_resumes_the_same_workspace(tmp_path, monkeypatch):
    # Owner rule (2026-10-05): one platform = one branch. Continue on a FAILED
    # run resumes it in place (fresh rework budget); only Start over is fresh.
    state = _failed_lettings_state(tmp_path)
    prior = Path(state.product_design.generation["output_dir"])
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["output_dir"] = str(output_dir)
        captured["cycle"] = cycle
        captured["product_id"] = bp.product_id
        captured["start_over"] = _kwargs.get("start_over", False)
        assert Path(output_dir) == prior
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "cycle": cycle or "code",
            "build": {"state": "building", "phases_done": 0, "phases_total": 5},
            "fresh_workspace": True,
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    result = platform_chat_flow.start_or_resume_coder(state)
    assert result.get("fresh") is False
    assert result.get("resumed") is True
    assert "same blueprint hash" not in (result.get("summary") or "").lower()
    assert "not starting over" not in (result.get("summary") or "").lower()
    assert "resum" in (result.get("summary") or "").lower()
    assert captured["start_over"] is False
    assert captured["output_dir"] == str(prior)
    assert state.product_design.generation["output_dir"] == captured["output_dir"]


def test_resume_generation_after_terminal_resumes_in_place(tmp_path, monkeypatch):
    state = _failed_lettings_state(tmp_path)
    prior = Path(state.product_design.generation["output_dir"])
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["out"] = str(output_dir)
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": "lettings-same-hash",
            "product_id": bp.product_id,
            "build": {"state": "building"},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    result = platform_chat_flow.resume_generation(state)
    assert result.get("fresh") is False and result.get("resumed") is True
    assert captured["out"] == str(prior)
    assert "same blueprint hash" not in result["summary"].lower()


def test_session_facts_after_terminal_failure_allow_draft(tmp_path):
    state = _failed_lettings_state(tmp_path)
    facts = platform_chat_llm._session_facts(state)
    assert "FAILED" in facts
    assert "draft_platform" in facts
    assert "RESUMES this platform" in facts and "Start over" in facts
    assert "do not draft a new platform" not in facts.lower()


def test_start_runner_build_rotates_off_a_failed_ledger_only_on_start_over(tmp_path, monkeypatch):
    from app.factory.blueprint import CapabilitySpec, ProductBlueprint

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    dead = tmp_path / "residential-lettings"
    dead.mkdir()
    ledger = BuildLedger(dead / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc")
    ledger.append(EventKind.RUN_FAILED, detail="rework budget of 3 exhausted")

    started = []

    def _record_thread(self):
        started.append(self.name)

    monkeypatch.setattr("threading.Thread.start", _record_thread)
    bp = ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="residential-lettings",
        product_name="Lettings",
        vertical="residential_lettings",
        summary="lettings",
        capabilities=[
            CapabilitySpec(
                id="audit",
                description="audit",
                block_ids=["audit"],
                strategy_hint="REUSE",
            )
        ],
    )
    # Without Start over a failed run is re-entered in place, never replaced,
    # and the resume resets the rework budget in the ledger.
    resumed = start_runner_build(bp, dead)
    assert resumed["fresh_workspace"] is False
    assert Path(resumed["output_dir"]) == dead
    from app.factory.build.rule_decision import BUDGET_RESET_KEY as REWORK_BUDGET_RESET

    assert any((e.payload or {}).get(REWORK_BUDGET_RESET) for e in BuildLedger(dead / "build_ledger.jsonl").events())
    ledger.append(EventKind.RUN_FAILED, detail="failed again")
    # Start over -- the only fresh door -- rotates to a new workspace.
    result = start_runner_build(bp, dead, start_over=True)
    assert result["already_running"] is False
    assert result["fresh_workspace"] is True
    assert Path(result["output_dir"]) != dead
    assert Path(result["output_dir"]).name == "residential-lettings__run2"
    assert started


@pytest.mark.asyncio
async def test_chat_new_brief_after_failed_run_drafts_not_resumes(tmp_path, monkeypatch):
    from app.core import session_store

    state = _failed_lettings_state(tmp_path)
    session_store._session_store[state.session_id] = state
    captured = {"generate": 0, "decide": 0}

    def boom(*_a, **_k):
        captured["generate"] += 1
        raise AssertionError("generate_product must not run on a new brief after terminal failure")

    def sneaky_start_coder(_state, _message):
        captured["decide"] += 1
        return {
            "action": "start_coder",
            "brief": "",
            "refine": {"op": "", "value": ""},
            "message": "",
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", boom)
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    monkeypatch.setattr(platform_chat_llm, "decide", sneaky_start_coder)

    try:
        events = await _collect_events(
            state.session_id,
            "build me a platform for residential lettings in Manchester",
            action="draft",
        )
    finally:
        session_store._session_store.pop(state.session_id, None)

    text = " ".join(
        str(e["data"]) for e in events if e["event"] in {"delta", "generation", "info", "blueprint"}
    ).lower()
    assert captured["generate"] == 0
    assert captured["decide"] == 0
    assert "same blueprint hash" not in text
    assert "not starting over" not in text
    assert "blueprint" in [e["event"] for e in events]
    assert "generation" not in [e["event"] for e in events]


@pytest.mark.asyncio
async def test_chat_continue_after_failed_run_resumes_it(tmp_path, monkeypatch):
    from app.core import session_store

    state = _failed_lettings_state(tmp_path)
    prior = state.product_design.generation["output_dir"]
    session_store._session_store[state.session_id] = state
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["out"] = str(output_dir)
        captured["cycle"] = cycle
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": "lettings-same-hash",
            "product_id": bp.product_id,
            "cycle": cycle or "code",
            "build": {"state": "building", "phases_done": 0, "phases_total": 5},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    try:
        events = await _collect_events(state.session_id, "", action="continue")
    finally:
        session_store._session_store.pop(state.session_id, None)

    text = " ".join(
        str(e["data"]) for e in events if e["event"] in {"delta", "generation", "info"}
    ).lower()
    assert captured.get("out") == prior
    assert "same blueprint hash" not in text
    assert "resum" in text
    assert "generation" in [e["event"] for e in events]
