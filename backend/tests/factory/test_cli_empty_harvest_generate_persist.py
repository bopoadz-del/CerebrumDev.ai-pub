"""DeepSeek-ready CLI exit 0 + empty harvest must still persist GENERATE.

#374–#377 honesty stays: no thin SUCCESS, no CLI authorship for factory
persist fill, no OpenRouter fallthrough while DeepSeek is ready.
Do not claim pilot_zip / founding.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.budget_inspect import STAGE_1_S, inspect_build
from app.factory.build.coder_session import (
    NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
    NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS,
    dispatch_compiled_brief,
    harvest_unkeepable_event_bus_ids,
    inventory_gap_ids,
    should_factory_grounded_generate_persist,
    should_factory_llm_generate_gaps,
    thin_stub_success_blocked,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.persist_accept import FACTORY_GROUNDED_PERSIST_SOURCE
from app.factory.build.roles import RoleContext, RoleError, run_writer
from app.factory.build.runner import Outcome, RoleRunner
from app.factory.build.workspace import RoleWorkspace
from app.factory.blueprint import load_blueprint
from tests.factory.test_deepseek_code_cli import _arm_deepseek_cli, _ctx


class _Gap:
    capability_id = "vetcare_hub_veterinary_core"
    block_ids = ()
    strategy = "GENERATE"
    notes = "core"


class _Audit:
    capability_id = "audit"
    block_ids = ("audit",)
    strategy = "REUSE"
    notes = "reuse"


class _Dash:
    capability_id = "dashboard"
    block_ids = ("dashboard",)
    strategy = "REUSE"
    notes = "reuse"


class _Plan:
    capabilities = (_Gap(), _Audit(), _Dash())


class _VetCare:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "GENERATE persist after empty DeepSeek CLI harvest"


def _compiled():
    plan = _Plan()
    compiled = compile_brief(_VetCare(), plan, store_ids={"audit", "dashboard"})
    assert inventory_gap_ids(compiled) == ["vetcare_hub_veterinary_core"]
    return compiled


def test_should_not_reopen_openrouter_when_deepseek_ready(tmp_path, monkeypatch):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    compiled = _compiled()
    from app.factory.build.coder_session import DispatchResult

    ok = DispatchResult(via="cli", ok=True, detail="done")
    assert should_factory_llm_generate_gaps(compiled, ok) is False
    assert should_factory_grounded_generate_persist(compiled, ok) is True


def test_dispatch_empty_cli_harvest_emits_generate_persist_not_openrouter(
    tmp_path, monkeypatch
):
    """CLI exit 0 + empty keepable harvest + GENERATE gap → persist envelope."""
    _arm_deepseek_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    ctx = _ctx(tmp_path)
    ctx.blueprint = _VetCare()
    ctx.plan = _Plan()
    compiled = _compiled()
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.via == "cli"
    assert result.ok, result.detail
    assert oneshot == []
    assert result.factory_llm_generate_fallthrough is False
    assert result.factory_llm_written_ids == []
    assert list(result.cli_authored_ids) == []
    assert result.blocker == NAMED_BLOCKER_CLI_NO_AUTHORSHIP
    assert "vetcare_hub_veterinary_core" in result.generate_persist_ids
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["via"] == "cli"
    assert receipt["cli_authored_ids"] == []
    assert receipt["factory_llm_generate_fallthrough"] is False
    assert receipt["factory_llm_written_ids"] == []
    assert "vetcare_hub_veterinary_core" in receipt["generate_persist_ids"]
    assert receipt["inventory_gaps"] == []
    assert "pilot_zip" not in json.dumps(receipt)
    persist = (
        tmp_path / "build" / "app" / "actions" / "vetcare_hub_veterinary_core.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in persist
    assert "def handle" in persist
    assert FACTORY_GROUNDED_PERSIST_SOURCE in persist
    assert "FACTORY_CODE_CLI" not in persist
    assert "deterministic contract template" not in persist
    assert "minimax" not in persist


def test_empty_harvest_generate_persist_still_refuses_thin_success(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="veterinary-care", inputs_hash="abc")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler vetcare_hub_veterinary_core (factory-grounded persist)",
        payload={
            "stage": "handlers",
            "capability": "vetcare_hub_veterinary_core",
            "source": "factory-grounded persist",
        },
    )
    state = {
        "brief_dispatch": {
            "via": "cli",
            "ok": True,
            "cli_authored_ids": [],
            "handler_ids": [],
            "factory_llm_written_ids": [],
            "generate_persist_ids": ["vetcare_hub_veterinary_core"],
            "blocker": NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        }
    }
    snap = inspect_build(ledger, state=state)
    blocker = thin_stub_success_blocked(
        snapshot=snap,
        elapsed_s=STAGE_1_S + 30.0,
        state=state,
        ledger=ledger,
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in blocker
    assert "pilot_zip" not in blocker

    root = Path(__file__).resolve().parents[3]
    runner = RoleRunner(
        load_blueprint(root / "blueprints/examples/runner_smoke.yaml"),
        out,
        ledger=ledger,
    )
    runner._run_started = runner.clock() - (STAGE_1_S + 30.0)
    runner.state["brief_dispatch"] = state["brief_dispatch"]
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is False
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in (outcome.detail or "")
    assert "pilot_zip" not in (outcome.detail or "")


def test_event_bus_unkeepable_handle_is_not_never_wrote(tmp_path, monkeypatch):
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "mkdir -p app/actions\n"
        "cat > app/actions/vetcare_hub_veterinary_core.py << 'EOF'\n"
        "CAPABILITY_ID = 'vetcare_hub_veterinary_core'\n"
        "def handle(payload):\n"
        "    steps = [{'block': 'event_bus', 'input': payload}]\n"
        "    return execute('workflow', {'steps': steps})\n"
        "EOF\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _arm_deepseek_cli(tmp_path, monkeypatch, script)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    ctx = _ctx(tmp_path)
    ctx.blueprint = _VetCare()
    ctx.plan = _Plan()
    compiled = _compiled()
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert oneshot == []
    assert result.factory_llm_generate_fallthrough is False
    assert list(result.cli_authored_ids) == []
    assert "vetcare_hub_veterinary_core" in result.cli_unkeepable_event_bus_ids
    assert result.blocker == NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP not in (result.detail or "")
    assert "vetcare_hub_veterinary_core" in result.generate_persist_ids
    persist = (
        tmp_path / "build" / "app" / "actions" / "vetcare_hub_veterinary_core.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in persist
    assert "def handle" in persist
    assert FACTORY_GROUNDED_PERSIST_SOURCE in persist
    assert "{'block': 'event_bus', 'input': payload}" not in persist


def test_harvest_classifies_unprepared_event_bus_as_unkeepable(tmp_path):
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "vetcare_hub_veterinary_core.py").write_text(
        "CAPABILITY_ID = 'vetcare_hub_veterinary_core'\n"
        "def handle(payload):\n"
        "    steps = [{'block': 'event_bus', 'input': payload}]\n"
        "    return execute('workflow', {'steps': steps})\n",
        encoding="utf-8",
    )
    found = harvest_unkeepable_event_bus_ids(
        root, ["vetcare_hub_veterinary_core"]
    )
    assert found == ["vetcare_hub_veterinary_core"]


def test_writer_deepseek_ready_empty_cli_stages_generate_persist(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    compiled = _compiled()
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "writer-persist"
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=RoleWorkspace(BuildRole.WRITER, out),
                blueprint=_VetCare(),
                plan=_Plan(),
                state={
                    "resolved_blocks": ("audit", "dashboard"),
                    "vendored_blocks": ("audit", "dashboard"),
                },
            )
        )
    # 0.5: deepseek-ready empty harvest lands zero coding-agent artifacts
    # -- the refusal is the named reason; the generate-persist evidence
    # still lands before it.
    assert "writer_no_output" in str(exc.value)
    assert oneshot == []
    handler = (
        out / "app" / "actions" / "vetcare_hub_veterinary_core.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in handler
    assert "def handle" in handler
    assert FACTORY_GROUNDED_PERSIST_SOURCE in handler
    assert "deterministic contract template" not in handler
    assert "FACTORY_CODE_CLI" not in handler
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text())
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_NO_AUTHORSHIP
    assert receipt["cli_authored_ids"] == []
    assert receipt["factory_llm_generate_fallthrough"] is False
    assert "vetcare_hub_veterinary_core" in receipt["generate_persist_ids"]
    assert "pilot_zip" not in json.dumps(receipt)
