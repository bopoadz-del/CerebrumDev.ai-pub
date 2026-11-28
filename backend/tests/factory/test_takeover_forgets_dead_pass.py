"""A new runner that takes over a ledger forgets the dead runner's writer pass.

The reviewer's sequence (grade on #297 at 2c38287, fault 1), driven through
two real takeover doors: Try again (``platform_chat_flow.start_or_resume_coder``)
and the boot-time orphan resume (``orphan_recovery.resume_orphaned_model_call``),
both -> ``start_runner_build`` -> ``RoleRunner.run`` with recording roles on an
inline thread. Nothing is appended to the ledger by hand after the stall.
"""

from __future__ import annotations

import os
import time

import pytest

from app.factory.build import writer_continuation as wc
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from tests.factory.test_branch_is_platform import STORE, _failed_platform


@pytest.mark.parametrize(
    "kind, payload, took_over",
    [
        # the note every RoleRunner.run writes before its first phase
        ("NOTE", {wc.RUNNER_START_KEY: True, "budget_class": "small"}, True),
        ("NOTE", {wc.RUNNER_START_KEY: True, "budget_class": "new_platform"}, True),
        # the key present but false, or on another kind: no runner started
        ("NOTE", {wc.RUNNER_START_KEY: False}, False),
        ("PHASE_STARTED", {wc.RUNNER_START_KEY: True}, False),
        ("GATE_PASSED", {wc.RUNNER_START_KEY: True}, False),
        ("REWORK", {wc.RUNNER_START_KEY: True}, False),
        ("NOTE", {"budget_class": "small"}, False),
    ],
)
def test_runner_took_over_reads_the_runner_start_note(kind, payload, took_over):
    assert wc._runner_took_over(kind, payload) is took_over


@pytest.mark.parametrize("wall", [900.0, 3600.0])
def test_the_runner_start_note_is_the_one_every_run_writes(tmp_path, wall):
    """The key the reader takes as a runner start is the one
    ``RoleRunner._note_plan_budget`` writes, and that note ends an earlier
    runner's continued pass on the card."""
    from types import SimpleNamespace

    from app.factory.build.runner import BuildBudget, RoleRunner

    led = BuildLedger(tmp_path / "build_ledger.jsonl")
    led.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="continuing, pass 2 of 3: x",
        payload={wc.NOTE_KEY: {"go": True, "pass_next": 2, "max_passes": 3}},
    )
    assert wc.floor_view(list(led.events()))["continuing"] is True
    fake = SimpleNamespace(
        budget=BuildBudget(max_rework=1, wall_clock_s=wall, phase_wall_clock_s=600),
        plan=None,
        ledger=led,
    )
    RoleRunner._note_plan_budget(fake)
    events = list(led.events())
    assert [bool((e.payload or {}).get(wc.RUNNER_START_KEY)) for e in events] == [False, True]
    assert events[-1].payload["wall_clock_s"] == wall
    assert wc.floor_view(events) is None


def _continued_writer(tmp_path, sid, pass_next, max_passes, *, stalled, call_open=False):
    out, state = _failed_platform(tmp_path, sid, passed=("COLLECTOR", "CLONER"))
    p = out / "build_ledger.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines(True)[:-2]
    p.write_text("".join(lines), encoding="utf-8")
    led = BuildLedger(p)
    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    led.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail=f"continuing, pass {pass_next} of {max_passes}: x",
        payload={
            wc.NOTE_KEY: {
                "go": True,
                "pass_next": pass_next,
                "max_passes": max_passes,
                "writer_seconds": 3600,
                "ceiling_s": 9000,
            }
        },
    )
    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    if call_open:
        # The continued pass's coder call, left open by a process restart.
        led.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail="dispatching compiled brief via the coder CLI",
            payload={"stage": "dispatch", "model_call": True, "deadline_s": 7200.0},
        )
    if stalled:
        old = time.time() - 600
        os.utime(p, (old, old))
    return out, state


class _Inline:
    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
        self._t, self._a, self._k, self.name = target, args, kwargs or {}, name

    def start(self):
        self._t(*self._a, **self._k)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def _install_runner(monkeypatch, out):
    """Make the real runner run inline with recording roles; return the dict
    that receives the build card read inside the resumed WRITER role."""
    from app.factory import build_jobs as bj
    from app.factory import platform_chat_flow
    from app.factory.build.roles_models import RoleResult
    from app.factory.build.runner import BuildBudget, RoleRunner

    seen = {}

    def role_fn(role):
        def _role(ctx):
            if role is BuildRole.WRITER and "w" not in seen:
                monkeypatch.setattr(bj, "_live_runner_thread", lambda _o: True)
                seen["w"] = bj.build_status(out)
            return RoleResult(ok=True, detail=f"{role.value} ok")

        return _role

    def fake_run(blueprint, output_dir, blocks_root, cycle="code", tenant_store=None, brief="", inputs_hash=""):
        RoleRunner(
            blueprint,
            output_dir,
            roles={r: role_fn(r) for r in BuildRole},
            blocks_root=blocks_root,
            budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=600),
            inputs_hash=inputs_hash,
            brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}',
        ).run()

    monkeypatch.setattr(bj, "_run", fake_run)
    monkeypatch.setattr(bj.threading, "Thread", _Inline)
    monkeypatch.setattr("app.factory.build.coder_session.raise_if_cli_session_unready", lambda: None)
    monkeypatch.setattr(platform_chat_flow, "_blocks_root", lambda: STORE)
    return seen


def _try_again(monkeypatch, out, state):
    """Door 1: press Try again on the real chat path."""
    from app.factory import platform_chat_flow

    seen = _install_runner(monkeypatch, out)
    reply = platform_chat_flow.start_or_resume_coder(state)
    return reply, seen.get("w")


def _boot_resume(monkeypatch, out, state):
    """Door 2: the boot-time orphan resume of an interrupted WRITER."""
    from app.factory.blueprint import ProductBlueprint
    from app.factory.build import orphan_recovery
    from app.factory.build.runner import frozen_blueprint

    seen = _install_runner(monkeypatch, out)
    bp = frozen_blueprint(ProductBlueprint.model_validate(state.product_design.blueprint))
    reply = orphan_recovery.resume_orphaned_model_call(out, bp, tenant_identity="tester")
    return reply, seen.get("w")


@pytest.mark.parametrize("door", ["try_again", "boot_resume"])
@pytest.mark.parametrize("pass_next, max_passes", [(2, 3), (3, 4)])
def test_a_takeover_of_a_stalled_continued_build_forgets_the_dead_pass(
    tmp_path, monkeypatch, pass_next, max_passes, door
):
    from app.factory import build_jobs as bj

    out, state = _continued_writer(
        tmp_path, f"sess_{door[0]}{pass_next}", pass_next, max_passes,
        stalled=True, call_open=door == "boot_resume",
    )
    monkeypatch.setattr(bj, "_live_runner_thread", lambda _o: False)
    assert bj.build_status(out)["state"] == "stalled"

    take_over = {"try_again": _try_again, "boot_resume": _boot_resume}[door]
    reply, during = take_over(monkeypatch, out, state)

    assert reply.get("ok") is not False, reply
    assert during is not None, ("the resumed WRITER role never ran", reply)
    assert during["state"] == "building"
    assert during.get("writer_pass") is None, during.get("writer_pass")


@pytest.mark.parametrize("pass_next, max_passes", [(2, 3), (3, 4)])
def test_a_live_continued_pass_still_shows_its_line(tmp_path, monkeypatch, pass_next, max_passes):
    from app.factory import build_jobs as bj

    out, _state = _continued_writer(tmp_path, f"sess_l{pass_next}", pass_next, max_passes, stalled=False)
    monkeypatch.setattr(bj, "_live_runner_thread", lambda _o: True)
    st = bj.build_status(out)

    assert st["state"] == "building"
    wp = st.get("writer_pass") or {}
    assert wp.get("continuing") is True
    assert (wp.get("pass"), wp.get("max_passes")) == (pass_next, max_passes)
