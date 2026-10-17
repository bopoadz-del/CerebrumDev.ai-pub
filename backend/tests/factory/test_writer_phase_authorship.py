"""Three-phase WRITER harvest vs FACTORY_CODE_CLI_NO_AUTHORSHIP.

sess_5782f226 run7 (tip 22c89e5): writer_phase_frontend_rag cleared,
WRITER listed complete / TESTER reached, then stage_2 hard-stop
FACTORY_CODE_CLI_NO_AUTHORSHIP (written=0, stub_rate=0.0) after four
C-BRIEF shots of the same six Steward gaps.

run8 (tip 2389ade): merge helper existed but write_dispatch_receipt still
replaced a 10-id receipt with an empty later CLI; empty staging on
TESTER rework re-listed dest-landed gaps; last_event stuck on backend.

Root cause: later-phase CLI (plant/reuse) harvested written=0 and
replaced the run receipt; GENERATE ids never dropped from
cbrief_work_ids after a keepable plant; inspect used only last-shot
cli_authored_ids. Thin-template SUCCESS ban stays.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.budget_inspect import STAGE_2_S, inspect_decision
from app.factory.build.coder_session import (
    EMPTY_CLI_WORK_STATE_KEY,
    NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
    NAMED_BLOCKER_CLI_THIN_AUTHORSHIP,
    DispatchResult,
    cbrief_work_ids,
    harvest_factory_planted_ids,
    merge_writer_phase_dispatch,
    phase_authorship_miss,
    record_unauthored_cli_work,
    remaining_phase_cli_work,
    thin_stub_success_blocked,
    write_dispatch_receipt,
)
from app.factory.build.roles import RoleContext
from app.factory.build.workspace import RoleWorkspace
from app.factory.build.writer_phases import (
    WRITER_PHASE_BACKEND,
    WRITER_PHASE_FRONTEND_RAG,
    WRITER_PHASE_INTEGRATION,
    PhaseAcceptHalt,
    accept_writer_phase,
    later_writer_phases,
    should_dispatch_writer_phase,
)
from tests.factory.test_deepseek_code_cli import _arm_deepseek_cli


STEWARD_GAPS = (
    "estate_maintenance",
    "evidence_verifier",
    "readiness_engine",
    "portfolio_rollup",
    "composed_ops_loop",
    "human_authority_gate",
)


class _Cap:
    def __init__(self, cid, block_ids=(), strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids or [cid])
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Cerebrum Steward"
    product_id = "steward"
    vertical = "private_estate"
    summary = "Estate operations"


def _compiled_steward_gaps():
    caps = []
    for cid in STEWARD_GAPS:
        strategy = "GENERATE" if cid == "human_authority_gate" else "REUSE"
        caps.append(_Cap(cid, [cid], strategy))
    store = {cid for cid in STEWARD_GAPS if cid != "human_authority_gate"}
    return compile_brief(_Blueprint(), _Plan(*caps), store_ids=store)


def _factory_planted_handler(cid: str) -> str:
    return (
        f'"""Handler for capability {cid}.\n\n'
        "Written by the factory WRITER role (factory-grounded persist).\n"
        '"""\n'
        'AUTHORED_BY = "factory-grounded persist"\n'
        f"CAPABILITY_ID = {cid!r}\n\n"
        "def handle(payload):\n"
        '    return {"ok": True, "capability": CAPABILITY_ID}\n'
    )


def _plant_keepable(root: Path, cids) -> None:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    for cid in cids:
        (actions / f"{cid}.py").write_text(
            _factory_planted_handler(cid), encoding="utf-8"
        )


def test_cbrief_work_ids_drop_keepable_generate_on_disk(tmp_path):
    compiled = SimpleNamespace(
        inventory=[
            SimpleNamespace(
                capability_id="human_authority_gate",
                is_gap=True,
                handler_source="",
                verified_present=None,
            )
        ]
    )
    assert cbrief_work_ids(compiled) == ["human_authority_gate"]
    _plant_keepable(tmp_path, ["human_authority_gate"])
    assert cbrief_work_ids(compiled, tmp_path) == []


def test_cbrief_work_ids_drop_planted_reuse_gaps(tmp_path):
    compiled = _compiled_steward_gaps()
    assert set(STEWARD_GAPS) <= set(cbrief_work_ids(compiled, tmp_path))
    _plant_keepable(tmp_path, STEWARD_GAPS)
    assert cbrief_work_ids(compiled, tmp_path) == []


def test_record_empty_cli_work_skips_later_phase_dispatch(tmp_path):
    compiled = _compiled_steward_gaps()
    state = {}
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="exit 0",
        blocker=NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        cli_authored_ids=[],
    )
    record_unauthored_cli_work(state, STEWARD_GAPS, result)
    assert state[EMPTY_CLI_WORK_STATE_KEY] == list(STEWARD_GAPS)
    assert remaining_phase_cli_work(compiled, tmp_path, state) == []
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG,
        SimpleNamespace(via="cli"),
        remaining_work=remaining_phase_cli_work(compiled, tmp_path, state),
    ) is False


def test_merge_later_empty_shot_keeps_backend_planted():
    prior = DispatchResult(
        via="cli",
        ok=True,
        detail="backend planted",
        cli_authored_ids=["estate_maintenance"],
        factory_planted_ids=list(STEWARD_GAPS[1:]),
        kept_handler_ids=list(STEWARD_GAPS),
    )
    later = DispatchResult(
        via="cli",
        ok=True,
        detail=f"{NAMED_BLOCKER_CLI_NO_AUTHORSHIP}: empty later shot",
        blocker=NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        cli_authored_ids=[],
        factory_planted_ids=[],
    )
    merged = merge_writer_phase_dispatch(prior, later)
    assert "estate_maintenance" in merged.cli_authored_ids
    assert set(merged.factory_planted_ids) == set(STEWARD_GAPS[1:])
    assert merged.blocker != NAMED_BLOCKER_CLI_NO_AUTHORSHIP


def test_phase_authorship_miss_ignores_non_cli_preflight():
    compiled = _compiled_steward_gaps()
    result = DispatchResult(
        via="unavailable",
        ok=False,
        detail="no binary",
        blocker="FACTORY_CODE_CLI_UNAVAILABLE",
        cli_authored_ids=[],
    )
    assert phase_authorship_miss(compiled, result, None, STEWARD_GAPS) is None


def test_phase_authorship_miss_fails_open_work_immediately():
    compiled = _compiled_steward_gaps()
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="exit 0",
        cli_authored_ids=[],
        factory_planted_ids=[],
        kept_handler_ids=[],
    )
    miss = phase_authorship_miss(compiled, result, None, STEWARD_GAPS)
    assert miss
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in miss
    assert "estate_maintenance" in miss


def test_phase_authorship_miss_silent_when_plant_landed(tmp_path):
    compiled = _compiled_steward_gaps()
    _plant_keepable(tmp_path, STEWARD_GAPS)
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="exit 0",
        cli_authored_ids=[],
        factory_planted_ids=list(STEWARD_GAPS),
        kept_handler_ids=list(STEWARD_GAPS),
    )
    assert phase_authorship_miss(compiled, result, tmp_path, STEWARD_GAPS) is None


def test_harvest_counts_factory_planted_reuse(tmp_path):
    compiled = _compiled_steward_gaps()
    _plant_keepable(tmp_path, STEWARD_GAPS)
    planted = harvest_factory_planted_ids(tmp_path, compiled=compiled)
    assert set(planted) == set(STEWARD_GAPS)
    excluded = harvest_factory_planted_ids(
        tmp_path,
        compiled=compiled,
        dispatch={"generate_persist_ids": ["human_authority_gate"]},
    )
    assert "human_authority_gate" not in excluded
    assert set(STEWARD_GAPS) - {"human_authority_gate"} <= set(excluded)


def test_thin_stub_counts_factory_planted_not_false_no_authorship(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    snap = {
        "agent_written": 6,
        "cli_or_llm_written": 0,
        "templated": 0,
        "stub_rate": 0.0,
        "cli_attempted": True,
        "n_required": 6,
    }
    state = {
        "n_required": 6,
        "brief_dispatch": {
            "via": "cli",
            "ok": True,
            "cli_authored_ids": [],
            "handler_ids": [],
            "factory_planted_ids": list(STEWARD_GAPS),
            "kept_handler_ids": list(STEWARD_GAPS),
        },
    }
    blocker = thin_stub_success_blocked(
        snapshot=snap, elapsed_s=STAGE_2_S + 10.0, state=state
    )
    assert blocker is None or NAMED_BLOCKER_CLI_NO_AUTHORSHIP not in blocker


def test_thin_stub_empty_cli_without_plants_stays_no_authorship(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    blocker = thin_stub_success_blocked(
        snapshot={
            "agent_written": 0,
            "cli_or_llm_written": 0,
            "templated": 4,
            "stub_rate": 1.0,
            "cli_attempted": True,
        },
        elapsed_s=30.0,
        state={"brief_dispatch": {"via": "cli", "ok": True, "cli_authored_ids": []}},
    )
    assert blocker
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in blocker
    assert NAMED_BLOCKER_CLI_THIN_AUTHORSHIP not in blocker


def test_inspect_stage_2_planted_handlers_do_not_hard_stop_no_authorship(
    tmp_path, monkeypatch
):
    _arm_deepseek_cli(tmp_path, monkeypatch)
    state = {
        "n_required": 6,
        "brief_dispatch": {
            "via": "cli",
            "ok": True,
            "cli_authored_ids": [],
            "handler_ids": [],
            "factory_planted_ids": list(STEWARD_GAPS),
        },
    }
    snap = {
        "agent_written": 6,
        "cli_or_llm_written": 0,
        "templated": 0,
        "stub_rate": 0.0,
        "cli_attempted": True,
        "cli_finished": True,
        "cli_in_flight": False,
        "progressing": True,
        "contract_misses": [],
        "n_required": 6,
    }
    decided = inspect_decision(
        elapsed_s=STAGE_2_S + 10.0,
        current_wall_s=STAGE_2_S,
        snapshot=snap,
        stage="stage_2",
        state=state,
    )
    assert decided["decision"] != "hard_stop"
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP not in decided["reason"]


def _receipt_ctx(tmp_path: Path, *, dest=None, staging=None, state=None):
    dest = Path(dest or (tmp_path / "dest"))
    dest.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if staging is not None:
        kwargs["staging"] = Path(staging)
    ws = RoleWorkspace(BuildRole.WRITER, dest, **kwargs)
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(*[_Cap(cid) for cid in STEWARD_GAPS]),
        state=state if state is not None else {},
    )


def _compiled_receipt():
    compiled = _compiled_steward_gaps()
    return compiled


def test_write_receipt_empty_shot_keeps_prior_union(tmp_path):
    """Good harvest then empty CLI must keep union ids on disk."""
    compiled = _compiled_receipt()
    ctx = _receipt_ctx(tmp_path)
    good = DispatchResult(
        via="cli",
        ok=True,
        detail="backend planted",
        cli_authored_ids=["estate_maintenance"],
        factory_planted_ids=list(STEWARD_GAPS[1:]),
        kept_handler_ids=list(STEWARD_GAPS),
    )
    write_dispatch_receipt(ctx, compiled, good)
    ctx.state["brief_dispatch"] = good.to_dict()
    empty = DispatchResult(
        via="cli",
        ok=True,
        detail=f"{NAMED_BLOCKER_CLI_NO_AUTHORSHIP}: empty later shot",
        blocker=NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        cli_authored_ids=[],
        factory_planted_ids=[],
        shot_cli_authored_ids=[],
    )
    receipt = write_dispatch_receipt(ctx, compiled, empty)
    assert "estate_maintenance" in receipt["cli_authored_ids"]
    assert set(receipt["factory_planted_ids"]) == set(STEWARD_GAPS[1:])
    assert receipt.get("blocker") != NAMED_BLOCKER_CLI_NO_AUTHORSHIP
    disk = json.loads(
        (ctx.workspace.workspace / "docs" / "coder_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert "estate_maintenance" in disk["cli_authored_ids"]
    assert disk.get("cli_authored_ids") != []


def test_write_receipt_empty_shot_reads_destination_not_empty_staging(tmp_path):
    """Rework staging is empty; dest receipt must still union."""
    compiled = _compiled_receipt()
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    first = _receipt_ctx(tmp_path, dest=dest)
    good = DispatchResult(
        via="cli",
        ok=True,
        detail="committed harvest",
        cli_authored_ids=["estate_maintenance", "evidence_verifier"],
        factory_planted_ids=list(STEWARD_GAPS[2:]),
    )
    write_dispatch_receipt(first, compiled, good)
    rework = _receipt_ctx(tmp_path, dest=dest, staging=staging, state={})
    empty = DispatchResult(
        via="cli",
        ok=True,
        detail=f"{NAMED_BLOCKER_CLI_NO_AUTHORSHIP}: empty rework shot",
        blocker=NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        cli_authored_ids=[],
        shot_cli_authored_ids=[],
    )
    receipt = write_dispatch_receipt(rework, compiled, empty)
    assert "estate_maintenance" in receipt["cli_authored_ids"]
    assert "evidence_verifier" in receipt["cli_authored_ids"]
    assert receipt.get("blocker") != NAMED_BLOCKER_CLI_NO_AUTHORSHIP


def test_cbrief_work_ids_see_destination_keepable(tmp_path):
    compiled = _compiled_steward_gaps()
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    staging.mkdir()
    _plant_keepable(dest, STEWARD_GAPS)
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=staging)
    assert set(STEWARD_GAPS) <= set(cbrief_work_ids(compiled, staging))
    assert cbrief_work_ids(compiled, ws) == []
    assert remaining_phase_cli_work(compiled, ws, {}) == []
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG,
        SimpleNamespace(via="cli"),
        remaining_work=remaining_phase_cli_work(compiled, ws, {}),
    ) is False


def test_phase_gate_requires_prior_land_before_later_phases(tmp_path):
    compiled = _compiled_steward_gaps()
    ctx = _receipt_ctx(tmp_path)
    assert later_writer_phases() == (
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    )
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_gate"):
        accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    ctx.state["landed_writer_phases"] = [WRITER_PHASE_BACKEND]
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_gate"):
        accept_writer_phase(ctx, WRITER_PHASE_INTEGRATION, compiled)


def test_empty_needed_shot_fails_closed_without_wall(tmp_path):
    """Needed empty CLI is a phase miss; empty memory skips later dispatch."""
    compiled = _compiled_steward_gaps()
    state = {}
    result = DispatchResult(
        via="cli",
        ok=True,
        detail="exit 0",
        blocker=NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        cli_authored_ids=[],
        factory_planted_ids=[],
        shot_cli_authored_ids=[],
    )
    miss = phase_authorship_miss(compiled, result, tmp_path, STEWARD_GAPS)
    assert miss
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in miss
    record_unauthored_cli_work(state, STEWARD_GAPS, result)
    leftover = remaining_phase_cli_work(compiled, tmp_path, state)
    assert leftover == []
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG,
        SimpleNamespace(via="cli"),
        remaining_work=leftover,
    ) is False
    merged_prior = DispatchResult(
        via="cli",
        ok=True,
        detail="union after empty shot",
        cli_authored_ids=["estate_maintenance"],
        factory_planted_ids=[],
        shot_cli_authored_ids=[],
    )
    still_miss = phase_authorship_miss(
        compiled, merged_prior, tmp_path, STEWARD_GAPS
    )
    assert still_miss
    assert NAMED_BLOCKER_CLI_NO_AUTHORSHIP in still_miss
