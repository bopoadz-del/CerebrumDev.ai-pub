"""F-W2c: the coding-agent CLI's own per-turn budget follows the Factory's wall.

Live 2026-11-13 (pilot b34dfdc): the stage-1 inspect granted a producing
writer 1800s -> 5400s and stage 2 honoured it, but the CLI stopped itself at
"Per-turn wall-clock budget exhausted after 3843s (limit: 3600s)" -- the CLI
default, because the Factory never set ``CODEWHALE_TURN_WALL_CLOCK_SECS``.
The abort, with files on disk and authored=8, was booked ``writer_no_output``.

Ruling F-W2c: (1) the CLI turn budget is dispatched at the largest wall the
runner may grant the pass (phase-bounded, ceiling-capped), never the plan
wall; (2) a turn that ends without completing while files are on disk is
``writer_interrupted`` (files kept, the pass continues); ``writer_no_output``
is reserved for a pass that wrote nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build.budget_inspect import STAGE_1_S
from app.factory.build.codewhale_worker import (
    CLI_TURN_WALL_ENV,
    agent_file_paths,
    cli_turn_wall_secs,
)
from app.factory.build.ledger import EventKind
from app.factory.build.roles_handlers import (
    _run_writer_via_codewhale_worker,
    _writer_cli_turn_wall_s,
)
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _runner(tmp_path, *, plan, phase, ceiling=7200.0):
    clock = {"t": 0.0}
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=1,
            wall_clock_s=plan,
            phase_wall_clock_s=phase,
            hard_ceiling_s=ceiling,
        ),
        clock=lambda: clock["t"],
    )
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(product_id="p", inputs_hash="h")
    runner._run_started = 0.0
    runner._deadline = plan
    runner._deadline_box["at"] = plan
    runner._deadline_box["clock"] = lambda: clock["t"]
    runner._deadline_box["grant_bound_left"] = runner._grantable_wall_left
    return runner, clock


# -- (1) the dispatch carries a turn budget >= any wall the runner can apply --


@pytest.mark.parametrize(
    "plan,phase,ceiling,expected",
    [
        (STAGE_1_S, 5400.0, 7200.0, 5400.0),  # the pilot: small plan, 5400s phase
        (2400.0, 9000.0, 7200.0, 7200.0),  # phase above the ceiling: capped
    ],
)
def test_turn_budget_is_the_grantable_wall_not_the_plan_wall(
    tmp_path, plan, phase, ceiling, expected
):
    runner, clock = _runner(tmp_path, plan=plan, phase=phase, ceiling=ceiling)
    clock["t"] = 120.0  # dispatched two minutes into the run
    ctx = type("C", (), {"deadline_box": runner._deadline_box})()

    turn = _writer_cli_turn_wall_s(ctx)
    assert turn == pytest.approx(expected - 120.0)
    assert turn > plan - 120.0, "the CLI must not be dispatched with the plan wall"

    # Whatever a progress-evidenced grant applies, the turn already covers it.
    applied = runner._extend_wall(phase, progress_evidenced=True)
    assert applied == pytest.approx(expected)
    assert cli_turn_wall_secs(plan - 120.0, turn) >= applied - 120.0


@pytest.mark.parametrize(
    "timeout,bound,expected",
    [(1800.0, 5280.0, 5280), (5400.0, 3000.4, 5400), (1800.0, None, 1800)],
)
def test_turn_budget_never_below_the_dispatch_wall(timeout, bound, expected):
    assert cli_turn_wall_secs(timeout, bound) == expected


def test_no_bound_set_leaves_the_turn_at_the_dispatch_wall():
    for box in ({}, {"grant_bound_left": lambda: None}):
        ctx = type("C", (), {"deadline_box": box})()
        assert _writer_cli_turn_wall_s(ctx) is None


_NO_PROBE = "cli_turn_wall: no grant-bound probe; dispatch wall kept"
_PROBE_FAILED = "cli_turn_wall: grant-bound probe failed"


def _raises():
    raise RuntimeError("probe broke")


@pytest.mark.parametrize(
    "box,expected,line",
    [
        ({}, None, _NO_PROBE),
        ({"grant_bound_left": _raises}, None, _PROBE_FAILED + " (RuntimeError)"),
        ({"grant_bound_left": lambda: 900.0}, 900.0, None),
    ],
)
def test_the_two_no_bound_paths_log_their_own_line(caplog, box, expected, line):
    """Ruling (d): an absent probe and a probe that raised are told apart."""
    ctx = type("C", (), {"deadline_box": box})()
    with caplog.at_level("INFO", logger="cerebrumdev.factory.roles_handlers"):
        assert _writer_cli_turn_wall_s(ctx) == expected
    seen = [r.getMessage() for r in caplog.records if "cli_turn_wall:" in r.getMessage()]
    if line is None:
        assert seen == []
    else:
        assert len(seen) == 1 and seen[0].startswith(line)
        other = _PROBE_FAILED if line == _NO_PROBE else _NO_PROBE
        assert other not in seen[0]


@pytest.mark.parametrize("bound,expected", [(5400.0, "5400"), (None, "1800")])
def test_worker_stamps_the_turn_budget_into_the_cli_env(
    tmp_path, monkeypatch, bound, expected
):
    """The env reaches the CLI process: a granted 5400s wall dispatches
    ``CODEWHALE_TURN_WALL_CLOCK_SECS=5400``; no bound keeps the dispatch wall."""
    seen = {}

    class _Stop(Exception):
        pass

    def fake_popen(argv, **kw):
        seen["env"] = dict(kw.get("env") or {})
        raise _Stop()

    monkeypatch.setattr(codewhale_worker, "worker_cli_path", lambda: str(tmp_path / "cw"))
    monkeypatch.setattr(codewhale_worker, "_require_bound_tenant", lambda t: None)
    monkeypatch.setattr(
        "app.factory.build.agent_confine.confinement_popen_kwargs", lambda cwd, cli: {}
    )
    monkeypatch.setattr(codewhale_worker, "_dns_probe", lambda *a, **k: {})
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        codewhale_worker, "worker_job_slot", lambda *a, **k: _NullSlot()
    )
    with pytest.raises(_Stop):
        codewhale_worker.run_worker_job(
            "brief", tmp_path / "out", timeout_s=1800.0, turn_wall_s=bound
        )
    assert seen["env"][CLI_TURN_WALL_ENV] == expected


class _NullSlot:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# -- (2) a budget-ended turn with files on disk is resumable -----------------


def _ctx(tmp_path):
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=type(
            "B", (), {"product_id": "p", "product_name": "n", "vertical": "v", "summary": "s"}
        )(),
        plan=None,
        state={},
    )


def _budget_ended_receipt(status="failed"):
    base = {
        "status": status,
        "termination_reason": "turn_budget",
        "provider": "deepseek",
        "model": "m",
        "output": "",
        "tools": [],
        "error": "exec turn failed: Per-turn wall-clock budget exhausted",
        "error_category": None,
    }
    base["to_dict"] = lambda self: {
        k: v for k, v in type(self).__dict__.items() if k != "to_dict" and not k.startswith("__")
    }
    return type("R", (), base)()


def _fake_worker(files, status="failed"):
    def fake(prompt, dest, tenant_store=None, session_id="", product_id="",
             progress=None, timeout_s=None, live_time_left=None,
             slot_wait_left=None, on_slot_wait=None):
        # The worker's own dispatch audit file is never the agent's output.
        (Path(dest) / "docs").mkdir(parents=True, exist_ok=True)
        (Path(dest) / "docs" / "writer_argv.json").write_text("{}", encoding="utf-8")
        for i in range(files):
            target = Path(dest) / "app" / "models" / f"m{i}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"X = {i}\n", encoding="utf-8")
        return _budget_ended_receipt(status)

    return fake


@pytest.mark.parametrize("files", [3, 243])
def test_budget_ended_turn_with_files_is_interrupted_and_continues(
    tmp_path, monkeypatch, files
):
    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", _fake_worker(files)
    )
    ctx = _ctx(tmp_path)
    notes = []
    ctx.note = lambda text, **kw: notes.append(dict(kw, text=str(text)))

    result = _run_writer_via_codewhale_worker(ctx)
    assert result.ok is True, result.detail
    assert result.reason != "writer_no_output"
    booked = ctx.state["writer_interrupted"]
    assert booked["files_written"] == files
    assert [n for n in notes if n.get("reason") == "writer_interrupted"]
    # The files stay with the pass.
    assert (tmp_path / "build" / "app" / "models" / "m0.py").is_file()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_budget_ended_turn_with_no_files_is_writer_no_output(
    tmp_path, monkeypatch, status
):
    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.run_worker_job", _fake_worker(0, status)
    )
    ctx = _ctx(tmp_path)
    result = _run_writer_via_codewhale_worker(ctx)
    assert result.ok is False
    assert result.reason == "writer_no_output"
    assert "writer_interrupted" not in ctx.state


@pytest.mark.parametrize("names,count", [(["a.py", "docs/writer_prompt.txt"], 1), ([], 0)])
def test_agent_file_paths_skips_the_dispatch_audit(tmp_path, names, count):
    for rel in names:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    assert len(agent_file_paths(tmp_path)) == count


# -- one ledger key for the applied wall on the stage-inspect path -----------


@pytest.mark.parametrize("cli_in_flight", [True, False])
def test_stage_inspect_grant_books_wall_applied_s(tmp_path, monkeypatch, cli_in_flight):
    runner, clock = _runner(tmp_path, plan=STAGE_1_S, phase=5400.0)
    clock["t"] = STAGE_1_S
    decided = {"next_wall_s": 2700.0, "decision": "continue_stage_2",
               "cli_in_flight": cli_in_flight}
    monkeypatch.setattr(runner, "_stage_inspect", lambda **kw: dict(decided))
    monkeypatch.setattr(runner, "_maybe_cli_phase_ramp", lambda: None)
    runner._maybe_stage_inspect()
    keyed = [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and "wall_applied_s" in (e.payload or {})
    ]
    assert len(keyed) == 1
    assert "wall_applied_s=" in keyed[0].detail
    assert keyed[0].payload["wall_requested_s"] == 2700.0
    assert keyed[0].payload["wall_applied_s"] == float(runner.budget.wall_clock_s)



# -- the stall-retry wrapper forwards the turn bound only when one is set ----


@pytest.mark.parametrize("turn_wall_s", [5400.0, None])
def test_stall_retry_forwards_the_turn_bound_only_when_set(
    tmp_path, monkeypatch, turn_wall_s
):
    seen = {}

    def _fake_run(prompt, checkout_dir, **kw):
        seen.update(kw)
        return "receipt"

    monkeypatch.setattr(codewhale_worker, "run_worker_job", _fake_run)
    got = codewhale_worker.run_worker_job_with_stall_retry(
        "p", tmp_path, turn_wall_s=turn_wall_s
    )
    assert got == "receipt"
    if turn_wall_s is None:
        assert "turn_wall_s" not in seen
    else:
        assert seen["turn_wall_s"] == turn_wall_s


# -- no grant bound before the run starts -------------------------------------


@pytest.mark.parametrize("started,expected", [(None, None), (0.0, 5400.0)])
def test_grantable_wall_left_is_none_before_the_run_starts(
    tmp_path, started, expected
):
    runner, _clock = _runner(tmp_path, plan=STAGE_1_S, phase=5400.0)
    runner._run_started = started
    assert runner._grantable_wall_left() == expected


# -- the applied-wall NOTE marks a grant that moved nothing as denied --------


@pytest.mark.parametrize(
    "applied,before,denied", [(1800.0, 1800.0, True), (5400.0, 1800.0, False)]
)
def test_wall_applied_note_marks_a_denied_grant(tmp_path, applied, before, denied):
    runner, _clock = _runner(tmp_path, plan=STAGE_1_S, phase=5400.0)
    runner._emit_wall_applied(
        {"next_wall_s": 5400.0}, applied_wall_s=applied, before_wall_s=before
    )
    keyed = [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and "wall_applied_s" in (e.payload or {})
    ]
    assert len(keyed) == 1
    assert keyed[0].payload["wall_applied_s"] == applied
    assert bool(keyed[0].payload.get("wall_denied", False)) is denied
