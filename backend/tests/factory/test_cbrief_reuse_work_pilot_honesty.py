"""All-REUSE C-BRIEF work inventory + timeout-ledger inspect honesty.

sess_d10dfc2890f7487b (insurance-distribution, tip b0d1dac): C-BRIEF logged
gaps=[] on seven REUSE caps, inspect counted timeout_s= / timeouts= noise
as 7 timeouts, then hard-stopped at pilot_open with written=7,
contract_misses=0, pilot_ready=false while the runner still SUCCESS-ed.

GENERATE inventory_gaps stay GENERATE-only (OpenRouter fallthrough).
C-BRIEF work items are GENERATE gaps plus REUSE that still need hole-fill.
Do not claim founding / pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.budget_inspect import (
    CEILING_S,
    STAGE_1_S,
    STAGE_2_S,
    inspect_build,
    inspect_decision,
    reconcile_budget_inspect_after_success,
)
from app.factory.build_jobs import build_status
from app.factory.build.coder_session import (
    NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
    DispatchResult,
    cbrief_work_ids,
    dispatch_compiled_brief,
    inventory_gap_ids,
    remaining_cbrief_work_ids,
    reuse_inventory_ids,
)
from app.factory.build.failure_kinds import TIMEOUT, failure_kind
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import Outcome, RoleRunner
from app.factory.blueprint import load_blueprint
from tests.factory.test_deepseek_code_cli import _arm_deepseek_cli, _ctx

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

INSURE_CAPS = (
    "workflow",
    "team",
    "document_engine",
    "analytics",
    "notification",
    "capture",
    "validation",
)
INSURE_STORE = set(INSURE_CAPS)


class _ReuseCap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = [cid]
        self.strategy = "REUSE"
        self.notes = cid


class _InsurePlan:
    capabilities = tuple(_ReuseCap(cid) for cid in INSURE_CAPS)


class _InsureBlueprint:
    product_name = "InsureDistribute"
    product_id = "insurance-distribution"
    vertical = "insurance"
    summary = "Insurance distribution prove"


def _insure_compiled():
    compiled = compile_brief(
        _InsureBlueprint(), _InsurePlan(), store_ids=INSURE_STORE
    )
    assert inventory_gap_ids(compiled) == []
    assert reuse_inventory_ids(compiled) == list(INSURE_CAPS)
    return compiled


def _keepable_handler(cid: str) -> str:
    return (
        f"CAPABILITY_ID = {cid!r}\n"
        "def handle(payload):\n"
        "    return {\"ok\": True, \"id\": (payload or {}).get(\"id\")}\n"
    )


def _ledger_with_written(tmp_path: Path, *, timeouts_noise: bool = True) -> BuildLedger:
    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="insurance-distribution", inputs_hash="abc")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": 7230.0,
            "done": 0,
            "total": 1,
        },
    )
    if timeouts_noise:
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=(
                "FACTORY_CODE_CLI C-BRIEF dispatch via=cli command=/usr/local/bin/kimi "
                f"gaps=[] reuse={list(INSURE_CAPS)!r} timeout_s=7230"
            ),
            payload={"stage": "dispatch", "source": "coder CLI"},
        )
        ledger.append(
            EventKind.NOTE,
            detail=(
                "inspect stage_1: FACTORY_CODE_CLI in-flight (7230s watchdog, "
                "written=0, stub_rate=1.0) — bump wall 1800s → 2700s; not "
                "FACTORY_CODE_CLI_UNUSED"
            ),
            payload={"budget_inspect": True, "timeouts": [], "agent_written": 0},
        )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="FACTORY_CODE_CLI session finished",
        payload={"stage": "dispatch", "source": "coder CLI", "done": 1, "total": 1},
    )
    for cid in INSURE_CAPS:
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"kept CLI handler {cid} (coder CLI (/usr/local/bin/kimi))",
            payload={
                "stage": "handlers",
                "capability": cid,
                "source": "coder CLI (/usr/local/bin/kimi)",
            },
        )
    if timeouts_noise:
        ledger.append(
            EventKind.NOTE,
            detail=(
                "inspect pilot_open: hard-stop — cap=validation, written=7, "
                "templated=0, stub_rate=0.0, timeouts=7, contract_misses=0, "
                "pilot_ready=false (pilot_ready is false; no RUN_SUCCEEDED "
                "pilot cycle)"
            ),
            payload={"budget_inspect": True, "stage": "pilot_open"},
        )
    return ledger


def test_cbrief_work_ids_name_reuse_hole_fill_when_generate_gaps_empty():
    compiled = _insure_compiled()
    assert inventory_gap_ids(compiled) == []
    work = cbrief_work_ids(compiled)
    assert work == list(INSURE_CAPS)
    assert "validation" in work


def test_cbrief_work_ids_drop_keepable_reuse_on_disk(tmp_path):
    compiled = _insure_compiled()
    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "validation.py").write_text(
        _keepable_handler("validation"), encoding="utf-8"
    )
    work = cbrief_work_ids(compiled, root)
    assert "validation" not in work
    assert "workflow" in work
    assert inventory_gap_ids(compiled) == []


def test_a_timeout_is_counted_by_its_recorded_kind_never_by_its_words(tmp_path):
    """The words decide nothing: a reason that says "timed out" with no kind
    is not a timeout, and a typed TIMEOUT is one whatever its message says."""
    ledger = _ledger_with_written(tmp_path)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="zorblat handler: coder LLM timed out (prose only, no kind)",
        payload={},
    )
    ledger.append(
        EventKind.PHASE_ABORTED,
        role=BuildRole.WRITER,
        detail="zorblat phase stopped",
        payload={"failure_kind": TIMEOUT},
    )
    state = {
        "coder_failures": {"quux": "wall reached", "frob": "timed out (words only)"},
        "coder_failure_kinds": {"quux": TIMEOUT},
    }
    snap = inspect_build(ledger, tmp_path / "build", state)
    assert snap["timeouts"] == ["zorblat phase stopped", "quux: wall reached"]


def test_failure_kind_reads_the_exception_type_through_its_cause():
    from app.factory.coder import CoderTimeout

    def wrapped():
        try:
            raise CoderTimeout("anything")
        except CoderTimeout as inner:
            raise RuntimeError("phase failed") from inner

    try:
        wrapped()
    except RuntimeError as exc:
        assert failure_kind(exc) == TIMEOUT
    assert failure_kind(ValueError("timed out")) == ""


def test_inspect_written_plus_timeout_ledger_does_not_hard_stop_pilot(tmp_path):
    ledger = _ledger_with_written(tmp_path)
    state = {
        "brief_dispatch": {
            "via": "cli",
            "ok": True,
            "cli_authored_ids": list(INSURE_CAPS),
            "handler_ids": list(INSURE_CAPS),
        }
    }
    snap = inspect_build(ledger, tmp_path / "build", state)
    assert snap["agent_written"] == 7
    assert snap["templated"] == 0
    assert snap["contract_misses"] == []
    assert snap["timeouts"] == []
    decided = inspect_decision(
        elapsed_s=STAGE_2_S + 10.0,
        current_wall_s=STAGE_2_S,
        snapshot=snap,
        stage="pilot_open",
        state=state,
    )
    assert decided["decision"] != "hard_stop"
    assert decided["decision"] in {"continue_ceiling", "continue_pilot"}
    assert "timeout ledger is not a hard-stop" in decided["reason"] or (
        "timeout ledger" in decided["reason"] and "not a halt" in decided["reason"]
    )
    assert decided.get("next_wall_s") == CEILING_S


def test_inspect_stage_1_written_still_bumps_to_stage_2(tmp_path):
    ledger = _ledger_with_written(tmp_path, timeouts_noise=False)
    snap = inspect_build(ledger, tmp_path / "build")
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="stage_1",
    )
    assert decided["decision"] == "continue_stage_2"
    assert decided["next_wall_s"] == STAGE_2_S


def test_receipt_inventory_work_not_empty_when_reuse_needs_hole_fill(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw)
        or {"specs": {}, "handlers": {}, "model": "minimax/minimax-m3:free"},
    )
    ctx = _ctx(tmp_path)
    ctx.blueprint = _InsureBlueprint()
    ctx.plan = _InsurePlan()
    compiled = _insure_compiled()
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert oneshot == []
    assert inventory_gap_ids(compiled) == []
    assert cbrief_work_ids(compiled) == list(INSURE_CAPS)
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["inventory_gaps"] == []
    assert "inventory_work" in receipt
    # CLI exit 0 with no handlers: REUSE hole-fill still listed or filled.
    remaining = remaining_cbrief_work_ids(
        compiled, result, root=tmp_path / "build"
    )
    assert receipt["inventory_work"] == remaining
    assert "pilot_zip" not in json.dumps(receipt)


def test_hung_cli_still_harvests_keepable_reuse(tmp_path, monkeypatch):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: {"specs": {}, "handlers": {}, "model": "x"},
    )

    def _hung(ctx, compiled, timeout_s=0):
        root = tmp_path / "build"
        actions = root / "app" / "actions"
        actions.mkdir(parents=True, exist_ok=True)
        (actions / "validation.py").write_text(
            _keepable_handler("validation"), encoding="utf-8"
        )
        return DispatchResult(
            via="cli",
            ok=False,
            detail=f"{NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL}: wall",
            blocker=NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL,
        )

    monkeypatch.setattr(
        "app.factory.build.coder_session._run_cli_session", _hung
    )
    ctx = _ctx(tmp_path)
    ctx.blueprint = _InsureBlueprint()
    ctx.plan = _InsurePlan()
    compiled = _insure_compiled()
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.via == "cli"
    assert result.ok is False
    assert result.blocker == NAMED_BLOCKER_CLI_HUNG_KILLED_BY_WALL
    assert ctx.state["coder_failure_kinds"]["brief_dispatch"] == TIMEOUT
    assert "validation" in result.cli_authored_ids
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert "validation" in receipt["cli_authored_ids"]
    assert "validation" not in receipt["inventory_work"]
    assert "pilot_zip" not in json.dumps(receipt)


def test_auto_pilot_refuses_code_cycle_success_lie(tmp_path):
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        auto_pilot=True,
    )
    runner._run_started = runner.clock()
    runner.cycle = "code"
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT NOT RUN — persist; STORE NOT RUN — ops",
    )
    assert outcome.ok is False
    assert outcome.outcome.value == "FAILED_ROLE_ERROR"
    assert "SUCCESS+non-pilot lie" in (outcome.detail or "")
    terminal = runner.ledger.terminal_event()
    assert terminal is not None
    assert terminal.kind is EventKind.RUN_FAILED
    assert runner.ledger.pilot_ready() is False


def test_pilot_open_never_hard_stops_even_on_thin_unused():
    snap = {
        "agent_written": 0,
        "templated": 4,
        "stub_rate": 1.0,
        "timeouts": [],
        "contract_misses": [],
        "pilot_ready": False,
        "pilot_ready_blockers": ["pilot_ready is false"],
        "progressing": False,
        "cli_in_flight": False,
        "cli_finished": False,
        "cli_attempted": False,
    }
    decided = inspect_decision(
        elapsed_s=STAGE_1_S,
        current_wall_s=STAGE_1_S,
        snapshot=snap,
        stage="pilot_open",
    )
    assert decided["decision"] != "hard_stop"
    assert decided["decision"] == "observe_pilot_open"
    assert "Not a halt" in decided["reason"]
    assert decided.get("next_wall_s") is None


def test_inspect_file_templated_is_not_capability_templated(tmp_path):
    ledger = _ledger_with_written(tmp_path, timeouts_noise=False)
    docs = tmp_path / "build" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    sources = {cid: "coder CLI (/usr/local/bin/kimi)" for cid in INSURE_CAPS}
    for i in range(29):
        sources[f"template_{i}.py"] = "deterministic contract template"
    (docs / "build_provenance.json").write_text(
        json.dumps({"artifact_sources": sources}), encoding="utf-8"
    )
    snap = inspect_build(ledger, tmp_path / "build")
    assert snap["agent_written"] == 7
    assert snap["templated"] == 0
    assert snap["templated_caps"] == 0
    assert snap["authored_files"] == 7
    assert snap["templated_files"] == 29
    assert snap["artifact_files"] == 36


def test_stale_hard_stop_inspect_does_not_poison_store_green_status(tmp_path):
    ledger = _ledger_with_written(tmp_path)
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
        payload={"outcome": "SUCCESS", "cycle": "pilot", "pilot_ready": True},
    )
    assert ledger.pilot_ready() is True
    stale = {
        "decision": "hard_stop",
        "pilot_ready": False,
        "agent_written": 7,
        "templated": 0,
        "timeouts": ["noise"] * 7,
        "reason": "inspect pilot_open: hard-stop — written=7, timeouts=7",
    }
    reconciled = reconcile_budget_inspect_after_success(
        stale, pilot_ready=True
    )
    assert reconciled["pilot_ready"] is True
    assert reconciled["decision"] == "superseded_by_pilot_success"
    assert reconciled["mid_run_decision"] == "hard_stop"
    assert reconciled["superseded_by"] == "RUN_SUCCEEDED"

    # 0.5: build_status re-evaluates the authorship floor; this pilot
    # success carries 7 CLI-authored capabilities, so record them in the
    # provenance the floor reads (unmeasured is now below floor).
    docs = tmp_path / "build" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "build_provenance.json").write_text(
        json.dumps(
            {
                "artifact_sources": {
                    cid: "coder CLI (/usr/local/bin/kimi)" for cid in INSURE_CAPS
                }
            }
        ),
        encoding="utf-8",
    )

    status = build_status(tmp_path / "build")
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is True
    inspect = status.get("budget_inspect") or {}
    assert inspect.get("decision") != "hard_stop"
    assert inspect.get("pilot_ready") is True
    assert inspect.get("superseded_by") == "RUN_SUCCEEDED"


def test_pilot_success_closing_inspect_matches_ledger(tmp_path):
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        auto_pilot=False,
    )
    runner._run_started = runner.clock()
    runner.cycle = "pilot"
    runner.state["brief_dispatch"] = {
        "via": "cli",
        "ok": True,
        "cli_authored_ids": list(INSURE_CAPS),
    }
    for cid in INSURE_CAPS:
        runner.ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"kept CLI handler {cid} (coder CLI (/usr/local/bin/kimi))",
            payload={
                "stage": "handlers",
                "capability": cid,
                "source": "coder CLI (/usr/local/bin/kimi)",
            },
        )
    outcome = runner._finish(
        Outcome.SUCCESS,
        "CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
    )
    assert outcome.ok is True
    assert runner.ledger.pilot_ready() is True
    inspects = [
        e
        for e in runner.ledger.events()
        if (e.payload or {}).get("budget_inspect")
    ]
    assert inspects
    last = inspects[-1].payload
    assert last.get("stage") == "pilot_close"
    assert last.get("pilot_ready") is True
    assert last.get("decision") == "already_pilot_ready"
    # 0.5: the authorship floor is re-evaluated against the provenance;
    # this pilot success carries 7 CLI-authored capabilities.
    docs = tmp_path / "build" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "build_provenance.json").write_text(
        json.dumps(
            {
                "artifact_sources": {
                    cid: "coder CLI (/usr/local/bin/kimi)" for cid in INSURE_CAPS
                }
            }
        ),
        encoding="utf-8",
    )
    status = build_status(tmp_path / "build")
    assert status["pilot_ready"] is True
    assert status["budget_inspect"]["pilot_ready"] is True
