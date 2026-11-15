"""FG-8(1): a grader stop binds the resume.

The WRITER's own gate passes before the grader reads its tree. A grader
verdict that ends the run must become WRITER's terminal verdict, so a resume
re-enters WRITER (seeded with the grader's findings) on the tree the grader
refused -- never TESTER. A run whose grader CERTIFIED and whose TESTER then
failed still resumes at TESTER without another writer pass.

Every capability id, file and message below is invented.
"""

from __future__ import annotations

import pytest

from tests.factory.test_a_grader import _fake_pass, _fake_pass_harness, smoke_blueprint  # noqa: F401


def _recording_writer(captured: list):
    def writer(ctx):
        captured.append(tuple(str(i) for i in ctx.work_list))
        return _fake_pass(ctx)

    return writer


def _writer_terminal(ledger):
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import TERMINAL_KINDS

    last = None
    for event in ledger.events():
        if event.role is BuildRole.WRITER and event.kind in TERMINAL_KINDS:
            last = event
    return last


def _not_certified(item: str):
    from app.factory.build import grader as grader_mod

    return grader_mod.GraderVerdict(
        tier=grader_mod.NOT_CERTIFIED, items=(item,), writer_items=(item,)
    )


def _error(item: str):
    from app.factory.build import grader as grader_mod

    return grader_mod.GraderVerdict(tier=grader_mod.ERROR, items=(item,))


def _factory_owed(item: str):
    from app.factory.build import grader as grader_mod

    return grader_mod.GraderVerdict(
        tier=grader_mod.NOT_CERTIFIED, items=(item,), factory_owed=(item,)
    )


STOPS = [
    (_not_certified, "function grade FAIL: zorblat_check", "grader_not_certified"),
    (_not_certified, "function grade FAIL: plinket_check", "grader_not_certified"),
    (_error, "grader crashed: quuxbar outage", "grader_error"),
    (_error, "grader crashed: frobnic timeout", "grader_error"),
    (_factory_owed, "FACTORY owes baked literals in zorblat/stamped.py: ['Qux']", "grader_factory_fault"),
    (_factory_owed, "FACTORY owes baked literals in plinket/vendored.py: ['Vex']", "grader_factory_fault"),
]


@pytest.mark.parametrize("make, item, reason", STOPS)
def test_a_grader_stop_is_the_writers_terminal_verdict_and_resume_re_enters_writer(
    smoke_blueprint, tmp_path, monkeypatch, make, item, reason  # noqa: F811
):
    from app.factory.build import grader as grader_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(grader_mod, "run_grader", lambda **kwargs: make(item))
    roles = _fake_pass_harness(monkeypatch)
    first: list = []
    roles[BuildRole.WRITER] = _recording_writer(first)
    out = tmp_path / "build"
    runner = RoleRunner(smoke_blueprint, out, roles=roles)
    outcome = runner.run()
    assert not outcome.ok
    assert runner.ledger.terminal_event().kind is EventKind.RUN_FAILED

    terminal = _writer_terminal(runner.ledger)
    assert terminal.kind is EventKind.GATE_FAILED
    assert terminal.payload["gate"] == grader_mod.GRADER_GATE
    assert terminal.payload["reason"] == reason
    assert terminal.payload["grader_tier"] == make(item).tier
    assert terminal.payload["findings"] == [item]
    assert BuildRole.WRITER not in runner.ledger.completed_roles()
    assert runner.ledger.resume_point() is BuildRole.WRITER

    # Resume on the same workspace: the WRITER runs again first, handed what
    # the grader refused, and the grader reads the tree before TESTER.
    graded: list = []

    def certified(**kwargs):
        graded.append(True)
        return grader_mod.GraderVerdict(tier=grader_mod.CERTIFIED)

    monkeypatch.setattr(grader_mod, "run_grader", certified)
    resumed: list = []
    roles[BuildRole.WRITER] = _recording_writer(resumed)
    again = RoleRunner(smoke_blueprint, out, roles=roles)
    result = again.run()
    assert result.ok, result.detail
    assert len(resumed) == 1
    assert any(item in entry for entry in resumed[0])
    assert graded == [True]


@pytest.mark.parametrize("message", ["tester broke on zorblat", "tester broke on plinket"])
def test_certified_then_a_tester_failure_still_resumes_at_tester(
    smoke_blueprint, tmp_path, monkeypatch, message  # noqa: F811
):
    from app.factory.build import grader as grader_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import EventKind
    from app.factory.build.roles import RoleError
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(
        grader_mod, "run_grader", lambda **kwargs: grader_mod.GraderVerdict(tier=grader_mod.CERTIFIED)
    )
    roles = _fake_pass_harness(monkeypatch)

    def broken_tester(ctx):
        raise RoleError(message)

    roles[BuildRole.TESTER] = broken_tester
    out = tmp_path / "build"
    runner = RoleRunner(smoke_blueprint, out, roles=roles)
    outcome = runner.run()
    assert not outcome.ok
    assert _writer_terminal(runner.ledger).kind is EventKind.GATE_PASSED
    assert BuildRole.WRITER in runner.ledger.completed_roles()
    assert runner.ledger.resume_point() is BuildRole.TESTER

    resumed: list = []
    roles[BuildRole.WRITER] = _recording_writer(resumed)
    roles[BuildRole.TESTER] = _fake_pass
    again = RoleRunner(smoke_blueprint, out, roles=roles)
    result = again.run()
    assert result.ok, result.detail
    assert resumed == []
