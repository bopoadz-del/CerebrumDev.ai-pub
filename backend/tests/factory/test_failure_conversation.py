"""F5 -- failure becomes a conversation.

On any FAILED outcome the run explains itself from its own ledger (what was
tried, where it stopped, why); the model may only rephrase ledger facts and is
refused otherwise. The Floor then offers exactly three TYPED choices --
take_copy, continue_with_intake, start_over -- and chat text never triggers
any of them.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.factory.build import failure_narrative as fn
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import INPUTS_REBASED_KEY, BuildLedger, EventKind
from app.factory.build.rule_decision import BUDGET_RESET_KEY
from app.factory.floor_actions import (
    ACTION_SPEC_PATH,
    FAILURE_ACTIONS,
    RUN_ACTIONS,
    FloorAction,
    parse_action,
)
from app.core.session_store import get_session
from tests.factory.test_branch_is_platform import (
    ROOT,
    STORE,
    _failed_platform,
    _inline_runner,
)


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _ledger(tmp_path) -> BuildLedger:
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="zorblat_platform", inputs_hash="h0")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER, BuildRole.WRITER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail=f"{role.value} ok")
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.TESTER, detail="suite red",
                  payload={"check": "product_gate"})
    ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail="round 1",
                  payload={"gate": "TESTER", "check": "product_gate"})
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail="FAILED(TESTER, product_gate, tide_relay refused its own schema)",
        payload={"decision": {"gate": "TESTER", "check": "product_gate",
                              "finding": "tide_relay refused its own schema",
                              "reason": "SAME_FAILURE_TWICE", "class": "STOP"}},
    )
    return ledger


def _ids(ledger, kind=None):
    return [f"e{e.seq}" for e in ledger.events() if kind is None or e.kind is kind]


def _never_called(_messages):
    raise AssertionError("the model must not be called")


# -- the narrative -------------------------------------------------------------


def test_the_deterministic_narrative_is_always_present_and_names_the_stop(tmp_path):
    ledger = _ledger(tmp_path)
    n = fn.compose_failure_narrative(list(ledger.events()), mode="off", llm=_never_called)
    assert n.source == fn.SOURCE_LEDGER
    assert "COLLECTOR, CLONER and WRITER" in n.text
    assert "1 rework round" in n.text
    assert "Where it stopped: TESTER (product_gate)" in n.text
    assert "tide_relay refused its own schema" in n.text
    assert set(n.cites) <= set(_ids(ledger))


def test_a_model_sentence_naming_a_phase_the_ledger_never_ran_is_refused(tmp_path):
    ledger = _ledger(tmp_path)
    run_failed = _ids(ledger, EventKind.RUN_FAILED)[0]
    calls = []

    def invents(messages):
        calls.append(messages)
        return {"sentences": [{"text": "It stopped in STORE_MANAGER.", "cites": [run_failed]}]}

    notes = []
    n = fn.compose_failure_narrative(
        list(ledger.events()), mode="on", llm=invents, note=lambda d, **p: notes.append(p)
    )
    assert len(calls) == 2  # one regeneration with the refusals
    assert "YOUR LAST ANSWER WAS REFUSED" in calls[1][1]["content"]
    assert n.source == fn.SOURCE_LEDGER  # the deterministic fallback
    assert any("STORE_MANAGER" in r for r in n.refusals)
    assert notes and notes[-1]["architect"]["fallback"] is True
    assert notes[-1]["architect"]["entry"] == fn.ENTRY


@pytest.mark.parametrize(
    "sentence",
    [
        {"text": "The run stopped.", "cites": []},  # cites nothing
        {"text": "The run stopped.", "cites": ["e999"]},  # a fact the ledger lacks
        {"text": "It failed ui_end_to_end.", "cites": ["RUN"]},  # check it never ran
    ],
)
def test_uncited_or_invented_facts_are_refused(tmp_path, sentence):
    ledger = _ledger(tmp_path)
    if sentence["cites"] == ["RUN"]:
        sentence = {**sentence, "cites": _ids(ledger, EventKind.RUN_FAILED)}
    facts = fn.ledger_facts(list(ledger.events()))
    ok, errors = fn.validate_sentences({"sentences": [sentence]}, facts)
    assert ok is None and errors


def test_a_faithful_model_narrative_is_used_when_the_foreman_is_on(tmp_path):
    ledger = _ledger(tmp_path)
    passed = _ids(ledger, EventKind.GATE_PASSED)
    stop = _ids(ledger, EventKind.RUN_FAILED)

    def faithful(_messages):
        return {"sentences": [
            {"text": "COLLECTOR, CLONER and WRITER passed.", "cites": passed},
            {"text": "TESTER stopped it on product_gate.", "cites": stop},
        ]}

    on = fn.compose_failure_narrative(list(ledger.events()), mode="on", llm=faithful)
    assert on.source == fn.SOURCE_MODEL and on.attempts == 1
    shadow = fn.compose_failure_narrative(list(ledger.events()), mode="shadow", llm=faithful)
    assert shadow.source == fn.SOURCE_LEDGER  # shadow records, never shows


def test_a_failed_run_attaches_its_narrative(tmp_path, monkeypatch):
    from app.factory.blueprint import load_blueprint
    from app.factory.build.roles_models import RoleResult
    from app.factory.build.runner import BuildBudget, RoleRunner

    monkeypatch.setenv("FACTORY_FOREMAN", "off")
    roles = {r: (lambda ctx, r=r: RoleResult(ok=True, detail=f"{r.value} ok")) for r in BuildRole}
    roles[BuildRole.COLLECTOR] = lambda ctx: RoleResult(ok=False, detail="collector refused")
    out = tmp_path / "plat"
    RoleRunner(
        load_blueprint(ROOT / "blueprints" / "examples" / "runner_smoke.yaml"),
        out,
        roles=roles,
        blocks_root=STORE,
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=600),
    ).run()
    events = list(BuildLedger(out / "build_ledger.jsonl").events())
    failed_at = max(i for i, e in enumerate(events) if e.kind is EventKind.RUN_FAILED)
    attached = [e for e in events[failed_at:] if fn.NARRATIVE_KEY in (e.payload or {})]
    assert attached, "a FAILED run must carry its narrative"
    assert attached[-1].payload[fn.NARRATIVE_KEY]["source"] == fn.SOURCE_LEDGER
    assert fn.read_narrative(out)["text"]


def test_build_status_and_the_manifest_carry_the_narrative(client, tmp_path):
    out, _state = _failed_platform(tmp_path, "sess_f5_status")
    status = client.get("/v1/sessions/sess_f5_status/product/build-status").json()["build"]
    narrative = status["failure_narrative"]
    assert narrative and "Where it stopped: TESTER" in narrative["text"]
    pkg = client.get("/v1/sessions/sess_f5_status/product/package?as_is=1")
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(pkg.content)).read("MANIFEST.json"))
    assert manifest["failure_narrative"] == narrative
    assert manifest["certified"] is False


# -- the three typed actions ----------------------------------------------------


def test_the_three_failure_choices_are_typed_and_in_the_shared_spec():
    spec = json.loads((ROOT / ACTION_SPEC_PATH).read_text(encoding="utf-8"))["actions"]
    for action in (FloorAction.TAKE_COPY, FloorAction.CONTINUE_WITH_INTAKE, FloorAction.START_OVER):
        assert action in FAILURE_ACTIONS
        assert parse_action(action.value) is action
        assert spec[action.value] == {"value": None}
    # Only start_over runs anything; the other two never start a build.
    assert FloorAction.TAKE_COPY not in RUN_ACTIONS
    assert FloorAction.CONTINUE_WITH_INTAKE not in RUN_ACTIONS


@pytest.mark.parametrize("words", ["take a copy", "start over", "continue with new answers"])
def test_chat_text_triggers_no_failure_choice(client, tmp_path, monkeypatch, words):
    from app.factory import platform_chat_flow, platform_chat_llm

    sid = "sess_f5_words_" + words.replace(" ", "_")
    _failed_platform(tmp_path, sid)
    called = []
    for name in ("take_copy", "continue_with_intake", "start_over", "confirm_and_continue",
                 "start_fresh_generation", "resume_failed_platform"):
        monkeypatch.setattr(platform_chat_flow, name, lambda *a, _n=name, **k: called.append(_n) or {})
    monkeypatch.setattr(platform_chat_llm, "should_orchestrate", lambda *a, **k: False)
    resp = client.post(f"/v1/sessions/{sid}/chat", json={"message": words})
    assert resp.status_code == 200
    assert called == []
    assert get_session(sid).product_design.intake_reopened is False


def test_take_copy_answers_with_the_as_is_export(tmp_path):
    from app.factory import platform_chat_flow

    _out, state = _failed_platform(tmp_path, "sess_f5_copy")
    reply = platform_chat_flow.take_copy(state)
    assert reply["ok"] is True and reply["download"] == {"as_is": True}
    state.product_design.generation = None
    assert platform_chat_flow.take_copy(state)["ok"] is False  # nothing failed


def _resume_hooks(monkeypatch, seen):
    from app.factory import build_jobs, platform_chat_flow

    def via_runner(bp, output_dir, **kwargs):
        seen.append({"output_dir": Path(output_dir), **kwargs})
        kwargs.pop("start_over", None)
        return build_jobs.start_runner_build(bp, output_dir, **kwargs)

    monkeypatch.setattr(platform_chat_flow, "generate_product", via_runner)
    monkeypatch.setattr(platform_chat_flow, "_blocks_root", lambda: STORE)
    fresh = []
    monkeypatch.setattr(platform_chat_flow, "start_fresh_generation", lambda *a, **k: fresh.append(1))
    return fresh


def test_continue_with_unchanged_answers_resumes_the_same_branch_at_the_failing_phase(
    tmp_path, monkeypatch
):
    from app.factory import platform_chat_flow
    from app.factory.floor_actions import FloorAction as A

    out, state = _failed_platform(tmp_path, "sess_f5_same")
    ran: list = []
    _inline_runner(monkeypatch, ran)
    seen: list = []
    fresh = _resume_hooks(monkeypatch, seen)

    opened = platform_chat_flow.continue_with_intake(state)
    assert opened["ok"] is True and opened["intake"]["reopened"] is True
    assert opened["intake"]["proposal"]["build_level"] == "production"  # pre-filled
    assert ran == []  # nothing runs until Confirm

    reply = platform_chat_flow.confirm_and_continue(state)
    assert fresh == [] and seen[0]["output_dir"] == out
    assert reply.get("resumed") is True
    events = list(BuildLedger(out / "build_ledger.jsonl").events())
    assert not any((e.payload or {}).get(INPUTS_REBASED_KEY) for e in events)  # same inputs
    assert ran and ran[0] == "TESTER"  # the failing phase, not COLLECTOR
    assert any((e.payload or {}).get(BUDGET_RESET_KEY) for e in events)
    assert state.product_design.intake_reopened is False
    _ = A  # the typed action is the only door (see the chat tests above)


def test_continue_with_changed_answers_rebases_the_same_branch(tmp_path, monkeypatch):
    from app.factory import platform_chat_flow
    from app.factory.floor_actions import FloorAction as A

    out, state = _failed_platform(tmp_path, "sess_f5_changed")
    before = BuildLedger(out / "build_ledger.jsonl").inputs_hash()
    ran: list = []
    _inline_runner(monkeypatch, ran)
    seen: list = []
    fresh = _resume_hooks(monkeypatch, seen)

    platform_chat_flow.continue_with_intake(state)
    changed = platform_chat_flow.apply_intake_action(state, A.SET_BUILD_LEVEL, "pilot")
    assert changed["ok"] is True  # editable again while re-opened
    platform_chat_flow.confirm_and_continue(state)

    ledger = BuildLedger(out / "build_ledger.jsonl")
    events = list(ledger.events())
    rebased = [e for e in events if (e.payload or {}).get(INPUTS_REBASED_KEY)]
    assert len(rebased) == 1 and rebased[0].payload["previous_inputs_hash"] == before
    assert ledger.inputs_hash() != before
    assert fresh == [] and seen[0]["output_dir"] == out  # same workspace
    assert not (out.parent / f"{out.name}__run2").exists()
    assert ran and ran[0] == "COLLECTOR"  # new inputs: every phase anew
    assert any((e.payload or {}).get(BUDGET_RESET_KEY) for e in events)


def test_a_rebase_is_the_only_way_a_resume_takes_new_inputs(tmp_path):
    from app.factory.build.ledger import LedgerError

    ledger = _ledger(tmp_path)
    with pytest.raises(LedgerError):
        ledger.assert_resumable(inputs_hash="h1")  # the guard is unchanged
    ledger.rebase_inputs("h1", reason="intake changed")
    assert ledger.inputs_hash() == "h1"
    ledger.assert_resumable(inputs_hash="h1")
    with pytest.raises(LedgerError):
        ledger.assert_resumable(inputs_hash="h0")
    assert ledger.completed_roles() == set()
    assert ledger.terminal_event() is None  # the rebased run has not finished


def test_intake_stays_frozen_after_approval_unless_reopened(tmp_path):
    from app.factory import platform_chat_flow
    from app.factory.floor_actions import FloorAction as A

    _out, state = _failed_platform(tmp_path, "sess_f5_frozen")
    refused = platform_chat_flow.apply_intake_action(state, A.SET_BUILD_LEVEL, "pilot")
    assert refused["ok"] is False
