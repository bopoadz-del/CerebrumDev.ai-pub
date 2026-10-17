"""continue/resume must restart an interrupted coding run, not demand a pending blueprint.

Live hole: after takeover the blueprint is approved and generation is
mid-flight (22/28 artifacts) or the worker died. The Floor LLM was told
start_coder is forbidden, so "continue" came back as "no blueprint pending"
instead of calling generate on the same hash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory import platform_chat_flow, platform_chat_llm
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.models.session import ProductDesignState, SessionState


def _state_with_approved_run(tmp_path: Path, *, succeeded: bool = False) -> SessionState:
    from app.factory.product_architect import draft_blueprint_from_brief

    out = tmp_path / "automotive-retail"
    out.mkdir()
    (out / "app").mkdir()
    (out / "app" / "writer_progress.txt").write_text("22 of 28", encoding="utf-8")

    ledger = BuildLedger(out / "build_ledger.jsonl")
    inputs_hash = "231361dfa711same"
    ledger.start_run(product_id="automotive-retail", inputs_hash=inputs_hash)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.COLLECTOR, detail="COLLECTOR")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.COLLECTOR, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.CLONER, detail="CLONER")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.CLONER, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    if succeeded:
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER, detail="ok")
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.TESTER, detail="ok")
        ledger.append(EventKind.PHASE_STARTED, role=BuildRole.STORE_MANAGER, detail="SM")
        ledger.append(EventKind.GATE_PASSED, role=BuildRole.STORE_MANAGER, detail="ok")
        ledger.append(EventKind.RUN_SUCCEEDED, detail="all phase gates passed")

    s = SessionState(session_id="sess-resume", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState()
    bp = draft_blueprint_from_brief("build a dealership command center")
    s.product_design.blueprint = bp.model_dump(mode="json")
    s.product_design.blueprint_approved = True
    s.product_design.build_level = "pilot"  # the user's typed choice
    s.product_design.generation = {
        "output_dir": str(out),
        "inputs_hash": inputs_hash,
        "product_id": bp.product_id,
        "engine": "runner",
        "build": {
            "state": "succeeded" if succeeded else "building",
            "phases_done": 5 if succeeded else 2,
            "phases_total": 5,
            "activity_done": 22,
            "activity_total": 28,
        },
        "phases_done": 5 if succeeded else 2,
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


@pytest.mark.parametrize("action", ["continue", "run_pilot"])
def test_resume_is_a_typed_action(action):
    from app.factory.floor_actions import RUN_ACTIONS, parse_action

    assert parse_action(action) in RUN_ACTIONS


def test_approved_incomplete_generation_is_resumable(tmp_path):
    state = _state_with_approved_run(tmp_path)
    assert not platform_chat_flow.has_pending_blueprint(state)
    assert platform_chat_flow.is_generation_resumable(state)
    assert not platform_chat_flow.is_generation_complete(state)


def test_successful_generation_is_not_resumable(tmp_path):
    state = _state_with_approved_run(tmp_path, succeeded=True)
    assert platform_chat_flow.is_generation_complete(state)
    assert not platform_chat_flow.is_generation_resumable(state)


def test_start_coder_on_approved_incomplete_resumes_not_pending_error(tmp_path, monkeypatch):
    """The live Floor reply was 'no blueprint pending'. A resumable run is
    resumed -- by the typed Continue action (owner ruling: the chat model's
    start_coder decision never builds; it points at the Continue control)."""
    state = _state_with_approved_run(tmp_path)
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["output_dir"] = str(output_dir)
        captured["product_id"] = bp.product_id
        assert (Path(output_dir) / "app" / "writer_progress.txt").read_text(
            encoding="utf-8"
        ) == "22 of 28"
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "build": {"state": "building", "phases_done": 2, "phases_total": 5},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    pointer = platform_chat_llm.apply_decision(
        state, "continue", {"action": "start_coder"}
    )
    assert pointer.get("awaiting_action") == "continue"
    assert "no blueprint" not in (pointer.get("summary") or "").lower()
    assert not captured, "the model's start_coder decision must not build"

    result = platform_chat_flow.start_or_resume_coder(state)
    assert "no blueprint" not in (result.get("summary") or "").lower()
    assert result.get("resumed") is True
    assert captured["output_dir"] == state.product_design.generation["output_dir"]
    assert state.product_design.generation["inputs_hash"] == "231361dfa711same"
    assert state.product_design.generation.get("resumed") is True


def test_continue_after_success_opens_pilot_on_same_workspace(tmp_path, monkeypatch):
    state = _state_with_approved_run(tmp_path, succeeded=True)
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["output_dir"] = str(output_dir)
        captured["cycle"] = cycle
        captured["product_id"] = bp.product_id
        assert (Path(output_dir) / "app" / "writer_progress.txt").read_text(
            encoding="utf-8"
        ) == "22 of 28"
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "cycle": cycle,
            "build": {"state": "building", "phases_done": 5, "phases_total": 5},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    result = platform_chat_flow.start_or_resume_coder(state)
    assert result.get("already_complete") is not True
    assert result.get("resumed") is True
    assert result.get("cycle") == "pilot"
    assert captured["cycle"] == "pilot"
    assert captured["output_dir"] == state.product_design.generation["output_dir"]
    assert "not a new product" in result["summary"].lower()


def _close_pilot_ready(out: Path) -> None:
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.open_pilot_cycle()
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.TESTER, detail="pilot green")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.STORE_MANAGER, detail="SM")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.STORE_MANAGER, detail="ops")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="all phase gates passed",
        payload={"cycle": "pilot", "pilot_ready": True},
    )


def _write_acceptance_kk(out: Path) -> None:
    from app.factory.build.store_acceptance import (
        ACCEPTANCE_CHECK_NAMES,
        AcceptanceLine,
        AcceptanceReport,
        write_acceptance_report,
    )

    write_acceptance_report(
        out,
        AcceptanceReport(
            passed=len(ACCEPTANCE_CHECK_NAMES),
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=True,
            lines=[AcceptanceLine(name=n, status="PASS") for n in ACCEPTANCE_CHECK_NAMES],
        ),
    )


def test_continue_after_pilot_ready_without_acceptance_reopens_measurement(
    tmp_path, monkeypatch
):
    """Pre-#395 Store-green is ledger-pilot only. Continue must re-run STORE."""
    state = _state_with_approved_run(tmp_path, succeeded=True)
    out = Path(state.product_design.generation["output_dir"])
    _close_pilot_ready(out)
    assert platform_chat_flow.is_pilot_ready(state) is False

    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["cycle"] = cycle
        captured["output_dir"] = str(output_dir)
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "cycle": cycle,
            "build": {"state": "building", "phases_done": 5, "phases_total": 5},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    result = platform_chat_flow.start_or_resume_coder(state)
    assert result.get("already_complete") is not True
    assert captured["cycle"] == "pilot"
    assert captured["output_dir"] == str(out)
    assert result.get("cycle") == "pilot"


def test_continue_after_pilot_ready_does_not_start_a_new_product(tmp_path, monkeypatch):
    state = _state_with_approved_run(tmp_path, succeeded=True)
    out = Path(state.product_design.generation["output_dir"])
    _close_pilot_ready(out)
    _write_acceptance_kk(out)
    assert platform_chat_flow.is_pilot_ready(state) is True

    def boom(*a, **k):
        raise AssertionError("generate_product must not run after pilot-ready")

    monkeypatch.setattr(platform_chat_flow, "generate_product", boom)
    result = platform_chat_flow.start_or_resume_coder(state)
    assert result.get("already_complete") is True
    assert result.get("pilot_ready") is True
    assert "pilot-ready" in result["summary"].lower()
    assert result.get("resumed") is not True


def test_resume_after_worker_restart_keeps_hash_and_workspace(tmp_path, monkeypatch):
    """New uvicorn worker: no live thread, ledger mid-WRITER, same hash.

    Must not empty the workspace (re-CLONER from zero).
    """
    state = _state_with_approved_run(tmp_path)
    out = Path(state.product_design.generation["output_dir"])
    prior_hash = state.product_design.generation["inputs_hash"]
    assert platform_chat_flow._live_build_thread(out) is None

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        # The generate door must be pointed at the existing tree.
        assert Path(output_dir) == out
        assert (out / "app" / "writer_progress.txt").exists()
        assert list(out.iterdir()), "workspace was emptied"
        ledger = BuildLedger(out / "build_ledger.jsonl")
        assert ledger.inputs_hash() == prior_hash
        assert ledger.resume_point() is BuildRole.WRITER
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": prior_hash,
            "product_id": bp.product_id,
            "build": {
                "state": "building",
                "phases_done": 2,
                "phases_total": 5,
                "activity_done": 22,
                "activity_total": 28,
            },
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    result = platform_chat_flow.resume_generation(state)
    assert result["resumed"] is True
    assert result["generation"]["inputs_hash"] == prior_hash
    assert (out / "app" / "writer_progress.txt").read_text(encoding="utf-8") == "22 of 28"
    assert result["generation"]["output_dir"] == str(out)
    assert result["generation"]["phases_done"] == 2


@pytest.mark.asyncio
async def test_chat_continue_resumes_instead_of_no_blueprint_pending(tmp_path, monkeypatch):
    from app.core import session_store

    state = _state_with_approved_run(tmp_path)
    session_store._session_store[state.session_id] = state
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["called"] = True
        captured["out"] = str(output_dir)
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "build": {"state": "building", "phases_done": 2, "phases_total": 5},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    # The typed Continue action is dispatched before the model is asked, so
    # a model that repeats the live refusal never surfaces that text.
    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: True)
    monkeypatch.setattr(
        platform_chat_llm,
        "decide",
        lambda s, m: {
            "action": "reply",
            "brief": "",
            "refine": {"op": "", "value": ""},
            "message": (
                "I can't resume a coding run because no blueprint is currently "
                "pending. If you want to build Dealership Command Center, please "
                "confirm the blueprint and I'll start the coding agent."
            ),
        },
    )

    try:
        events = await _collect_events(state.session_id, "", action="continue")
    finally:
        session_store._session_store.pop(state.session_id, None)

    text = " ".join(
        str(e["data"]) for e in events if e["event"] in {"delta", "generation", "info"}
    ).lower()
    assert captured.get("called") is True
    assert "no blueprint" not in text
    assert "pending" not in text or "resum" in text
    kinds = [e["event"] for e in events]
    assert "generation" in kinds
    payload = json.loads(next(e["data"] for e in events if e["event"] == "generation"))
    assert payload.get("resumed") is True


@pytest.mark.asyncio
async def test_chat_continue_after_success_opens_pilot(tmp_path, monkeypatch):
    from app.core import session_store

    state = _state_with_approved_run(tmp_path, succeeded=True)
    session_store._session_store[state.session_id] = state
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["cycle"] = cycle
        captured["out"] = str(output_dir)
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "cycle": "pilot",
            "build": {"state": "building", "phases_done": 5, "phases_total": 5},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    try:
        events = await _collect_events(state.session_id, "", action="continue")
    finally:
        session_store._session_store.pop(state.session_id, None)

    text = " ".join(
        str(e["data"]) for e in events if e["event"] in {"delta", "generation", "info"}
    ).lower()
    assert captured.get("cycle") == "pilot"
    assert "not a new product" in text
    assert "generation" in [e["event"] for e in events]


def test_session_facts_allow_start_coder_when_resumable(tmp_path):
    state = _state_with_approved_run(tmp_path)
    facts = platform_chat_llm._session_facts(state)
    assert "incomplete" in facts.lower()
    assert "forbidden" not in facts
    assert "start_coder" in facts


def test_session_facts_allow_start_coder_for_pilot_after_code_phase(tmp_path):
    state = _state_with_approved_run(tmp_path, succeeded=True)
    facts = platform_chat_llm._session_facts(state)
    assert "NOT pilot-ready" in facts
    assert "start_coder" in facts
    assert "forbidden" not in facts


def test_session_facts_require_start_coder_when_ledger_pilot_lacks_acceptance(
    tmp_path,
):
    state = _state_with_approved_run(tmp_path, succeeded=True)
    out = Path(state.product_design.generation["output_dir"])
    _close_pilot_ready(out)
    facts = platform_chat_llm._session_facts(state)
    assert "NOT pilot-ready" in facts
    assert "start_coder" in facts
    assert "forbidden" not in facts


def test_session_facts_forbid_start_coder_after_pilot_ready(tmp_path):
    state = _state_with_approved_run(tmp_path, succeeded=True)
    out = Path(state.product_design.generation["output_dir"])
    _close_pilot_ready(out)
    _write_acceptance_kk(out)
    facts = platform_chat_llm._session_facts(state)
    assert "pilot-ready" in facts
    assert "forbidden" in facts


def test_interrupted_pilot_without_terminal_still_resumes_as_pilot(tmp_path, monkeypatch):
    """An open (non-terminal) Store-green cycle still resumes as pilot."""
    state = _state_with_approved_run(tmp_path, succeeded=True)
    out = Path(state.product_design.generation["output_dir"])
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.open_pilot_cycle()
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")

    assert platform_chat_flow._resume_cycle(state) == "pilot"
    assert platform_chat_flow.is_generation_resumable(state)
    assert not platform_chat_flow.is_generation_terminal_failure(state)
    captured = {}

    def fake_generate(bp, output_dir, blocks_root=None, cycle=None, **_kwargs):
        captured["cycle"] = cycle
        captured["out"] = str(output_dir)
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": state.product_design.generation["inputs_hash"],
            "product_id": bp.product_id,
            "cycle": cycle,
            "build": {"state": "building"},
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)
    result = platform_chat_flow.resume_generation(state)
    assert captured["cycle"] == "pilot"
    assert captured["out"] == str(out)
    assert result.get("already_complete") is not True
    assert result.get("resumed") is True
    assert result.get("fresh") is not True
