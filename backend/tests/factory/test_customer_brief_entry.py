"""Chat, API, replay and generate_product carry one customer_brief.v1.

The customer's words are the ask. documents comes from the session's
uploads. A run started with that brief proceeds and the writer, the tester
and the grader quote it. A run started without one starts no phase.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.factory.platform_chat_flow as chat
import app.routers.session_product as api
from app.core.auth import DEV_LOCAL_TENANT
from app.core.session_store import create_session, get_session, update_session
from app.factory.build.authority import BuildRole
from app.factory.build.customer_brief import emit_brief, format_report, read_brief
from app.factory.build.grader import shown_customer_brief
from app.factory.build.ledger import EventKind
from app.factory.build.roles_handlers import (
    _compiled_writer_brief,
    quote_for_tester,
    smoke_header,
)
from app.factory.build.runner import Outcome, RoleRunner
from app.factory.planner import ProductPlan
from app.main import app
from app.models.session import SessionState

ASK_A = "Count the items on hand."
ASK_B = "upload a pdf please"


def _runner(tmp_path, brief):
    plan = ProductPlan(product_id="sample", capabilities=[], fail_closed=True)
    return RoleRunner(
        {
            "product_id": "sample",
            "product_name": "Sample",
            "summary": "items on hand",
            "capabilities": [],
        },
        tmp_path / "build",
        plan=plan,
        blocks_root=tmp_path / "blocks",
        inputs_hash="inputs",
        brief=brief,
        budget=__import__(
            "app.factory.build.runner", fromlist=["BuildBudget"]
        ).BuildBudget(max_rework=0, wall_clock_s=30),
    )


class _Ctx:
    def __init__(self, brief):
        self.state = {"brief": brief}
        self.blueprint = type("B", (), {"capabilities": [], "summary": ""})()
        self.workspace = type("W", (), {"exists": lambda self, _rel: False})()


def _same_quote(raw, ask):
    assert json.loads(raw)["ask"] == ask
    assert _compiled_writer_brief(_Ctx(raw)).startswith(raw)
    line = next(item for item in smoke_header(quote_for_tester(raw)) if item.startswith("TESTER_PROMPT"))
    assert json.loads(line.split("=", 1)[1].strip()) == raw
    assert shown_customer_brief(raw) == raw
    assert shown_customer_brief({"summary": "a factory contract"}) == ""


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.setenv("ARCHITECT_LLM_DRAFTING_ENABLED", "0")
    monkeypatch.setenv("FACTORY_STAGE_RESUME", "0")
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    return TestClient(app)


def _chat_brief(ask, files):
    state = SessionState(session_id="sess_entry_chat", user_id="u1")
    state.source_files = list(files)
    if files:
        state.chunks = ["a stored line"]
    chat.draft_from_chat(state, ask)
    return state


def _api_brief(client, ask, files):
    session_id = "sess_" + uuid.uuid4().hex[:12]
    create_session(session_id, DEV_LOCAL_TENANT)
    state = get_session(session_id)
    state.source_files = list(files)
    if files:
        state.chunks = ["a stored line"]
    update_session(session_id, state)
    response = client.post(
        f"/v1/sessions/{session_id}/product/draft",
        json={"brief": ask},
    )
    assert response.status_code == 200, response.text
    return get_session(session_id)


@pytest.mark.parametrize(
    "path, ask, files, uploads, types",
    [
        ("chat", ASK_A, ["notes.PDF", "sheet.xlsx"], True, ["pdf", "xlsx"]),
        ("chat", ASK_B, [], False, []),
        ("api", ASK_A, ["notes.PDF", "sheet.xlsx"], True, ["pdf", "xlsx"]),
        ("api", ASK_B, [], False, []),
    ],
)
def test_chat_and_api_briefs_pass_the_gate_and_reach_every_stage(
    client, tmp_path, monkeypatch, path, ask, files, uploads, types
):
    state = _chat_brief(ask, files) if path == "chat" else _api_brief(client, ask, files)
    raw = state.product_design.brief
    record, problems = read_brief(raw)
    assert problems == []
    assert record is not None
    assert record.ask == ask
    assert record.uploads is uploads
    assert list(record.file_types) == types
    assert raw == emit_brief(ask, files)

    captured = {}

    def _forward(blueprint, output_dir, **kwargs):
        captured["brief"] = kwargs.get("brief")
        return {
            "engine": "runner",
            "output_dir": str(output_dir),
            "inputs_hash": "h",
            "product_id": "sample",
            "build": {"state": "building"},
        }

    if path == "chat":
        monkeypatch.setattr(chat, "generate_product", _forward)
        monkeypatch.setattr(chat, "_blocks_root", lambda: tmp_path)
        monkeypatch.setattr(
            chat,
            "_compile_and_lint_approved",
            lambda _state, _bp: {
                "lint": type("L", (), {"ok": True, "to_dict": lambda self: {}})(),
                "plan": type("P", (), {"to_dict": lambda self: {}})(),
                "plain_language": "",
            },
        )
        chat.approve_and_generate(state, output_root=tmp_path)
        assert captured["brief"] == raw
    else:
        monkeypatch.setattr(api, "generate_product", _forward)
        monkeypatch.setattr(api, "resolve_blocks_root", lambda: tmp_path)
        monkeypatch.setattr(
            chat,
            "_compile_and_lint_approved",
            lambda _state, _bp: {
                "lint": type("L", (), {"ok": True, "errors": [], "to_dict": lambda self: {}})(),
                "plan": type("P", (), {"to_dict": lambda self: {"capabilities": []}})(),
                "plain_language": "",
            },
        )
        state.product_design.blueprint_approved = True
        state.product_design.build_level = "prototype"
        update_session(state.session_id, state)
        response = client.post(f"/v1/sessions/{state.session_id}/product/generate", json={})
        assert response.status_code == 200, response.text
        assert captured["brief"] == raw

    runner = _runner(tmp_path, raw)
    outcome = runner.run()
    assert "brief incomplete" not in (outcome.detail or "")
    started = [e.role for e in runner.ledger.events() if e.kind is EventKind.PHASE_STARTED]
    assert BuildRole.COLLECTOR in started
    assert runner.state["customer_brief_quote"] == raw
    _same_quote(raw, ask)


@pytest.mark.parametrize("entry", ["api", "replay"])
def test_a_run_with_a_brief_proceeds_and_one_without_starts_nothing(
    tmp_path, monkeypatch, entry
):
    """The door stays open. Only a missing brief stops the run."""
    from app.factory import product_architect
    from tests.factory.grader_replay import _runner as replay_runner

    valid = emit_brief(ASK_A, [])
    forwarded = {}

    if entry == "api":
        class _Planner:
            def __init__(self, *_a, **_k):
                pass

            def plan(self, _bp):
                return ProductPlan(product_id="sample", capabilities=[], fail_closed=True)

        monkeypatch.setattr(product_architect, "CapabilityPlanner", _Planner)
        monkeypatch.setattr(
            "app.factory.compliance_gate.assert_compliant", lambda *_a, **_k: None
        )
        monkeypatch.setattr("app.factory.build_jobs.build_engine", lambda: "runner")

        def _start(_bp, out, **kwargs):
            forwarded["brief"] = kwargs.get("brief")
            return {"engine": "runner", "output_dir": str(out), "inputs_hash": "h", "product_id": "p"}

        monkeypatch.setattr("app.factory.build_jobs.start_runner_build", _start)
        product_architect.generate_product(
            type("B", (), {"product_id": "sample"})(),
            tmp_path / "out",
            blocks_root=tmp_path,
            brief=valid,
        )
        assert forwarded["brief"] == valid
        product_architect.generate_product(
            type("B", (), {"product_id": "sample"})(),
            tmp_path / "out2",
            blocks_root=tmp_path,
        )
        assert forwarded["brief"] == ""
    else:
        seen = {}

        class _Fake:
            def __init__(self, *_a, **kwargs):
                seen["brief"] = kwargs.get("brief", "")

        monkeypatch.setattr("app.factory.build.runner.RoleRunner", _Fake)
        monkeypatch.setattr(
            "app.factory.blueprint.load_blueprint", lambda _path: {"product_id": "sample"}
        )
        replay_runner(tmp_path / "bp.yaml", tmp_path / "root", tmp_path / "store", brief=valid)
        assert seen["brief"] == valid
        replay_runner(tmp_path / "bp.yaml", tmp_path / "root", tmp_path / "store")
        assert seen["brief"] == ""

    proceeded = _runner(tmp_path / "yes", valid)
    outcome = proceeded.run()
    assert "brief incomplete" not in (outcome.detail or "")
    assert [e.role for e in proceeded.ledger.events() if e.kind is EventKind.PHASE_STARTED]
    assert proceeded.state["customer_brief_quote"] == valid
    _same_quote(valid, ASK_A)

    stopped = _runner(tmp_path / "no", "")
    refused = stopped.run()
    assert refused.outcome is Outcome.FAILED_GATE
    assert refused.detail == format_report(["missing brief"])
    assert [e.role for e in stopped.ledger.events() if e.kind is EventKind.PHASE_STARTED] == []
