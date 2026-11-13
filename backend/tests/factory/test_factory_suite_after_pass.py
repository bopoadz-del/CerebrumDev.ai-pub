"""F-S2b: the Factory runs the full suite once after a writer pass.

Every rule is pinned by two inputs that must disagree: a green suite vs a red
one, a runner that returns vs one that raises, a counted failure vs an
uncounted one, a pass whose tree carries tests vs one that carries none.
"""

from __future__ import annotations

import subprocess

import pytest

from app.factory.build.pytest_policy import (
    SUITE_FINDINGS_KEPT,
    factory_suite_after_pass,
    factory_suite_line,
)


def _product(root, body: str) -> None:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_thing.py").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "body,outcome,failures",
    [
        ("def test_ok():\n    assert 1 + 1 == 2\n", "green", 0),
        ("def test_bad():\n    assert 1 + 1 == 3\n", "suite_red", 1),
    ],
)
def test_the_factory_suite_reports_what_the_suite_did(tmp_path, body, outcome, failures):
    _product(tmp_path, body)
    record = factory_suite_after_pass(tmp_path)
    assert record["outcome"] == outcome, record
    assert record["failures"] == failures
    assert len(record["findings"]) == failures
    assert isinstance(record["elapsed_s"], float)


@pytest.mark.parametrize("raises", [False, True])
def test_a_runner_that_raises_is_recorded_as_error_never_green(tmp_path, raises):
    _product(tmp_path, "def test_ok():\n    assert True\n")

    def runner(argv, *, cwd, timeout):
        if raises:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)

    record = factory_suite_after_pass(tmp_path, runner=runner, timeout_s=120)
    if raises:
        assert record["outcome"] == "error"
        assert record["reason"] == "TimeoutExpired"
        assert record["failures"] is None
    else:
        assert record["outcome"] == "green", record


@pytest.mark.parametrize("failures,shown", [(None, "-"), (0, "0"), (3, "3")])
def test_ledger_line_never_reads_an_uncounted_run_as_zero(failures, shown):
    line = factory_suite_line({"outcome": "x", "failures": failures, "elapsed_s": 1.0})
    assert f"failures={shown} " in line
    assert line.startswith("factory suite after pass: suite=x ")


@pytest.mark.parametrize(
    "reason,count,outcome,kept",
    [
        ("", SUITE_FINDINGS_KEPT + 2, "failed", SUITE_FINDINGS_KEPT),
        ("suite_red", 3, "suite_red", 3),
    ],
)
def test_a_red_verdict_is_named_and_its_findings_capped(
    tmp_path, monkeypatch, reason, count, outcome, kept
):
    """An unnamed red verdict still reads as failed, and the ledger record
    keeps at most SUITE_FINDINGS_KEPT findings while counting them all."""
    from app.factory.build import gates
    from app.factory.build.gates import GateResult

    found = [f"FAILED tests/test_x.py::test_{i}" for i in range(count)]
    monkeypatch.setattr(
        gates,
        "gate_suite_green",
        lambda ctx: GateResult(ok=False, gate="suite_green", reason=reason, findings=found),
    )
    record = factory_suite_after_pass(tmp_path)
    assert record["outcome"] == outcome
    assert record["failures"] == count
    assert len(record["findings"]) == kept


@pytest.mark.parametrize("detail,suffix", [("", False), ("suite is red: E x", True)])
def test_ledger_line_carries_the_verdict_detail_when_there_is_one(detail, suffix):
    line = factory_suite_line({"outcome": "x", "failures": 1, "elapsed_s": 1.0, "detail": detail})
    assert line.endswith(" -- suite is red: E x") is suffix
    assert line.endswith("elapsed_s=1.0") is not suffix


@pytest.mark.parametrize("outcome,failures", [("green", 0), ("suite_red", 2)])
def test_writer_pass_records_one_factory_suite_after_the_session(
    tmp_path, monkeypatch, outcome, failures
):
    """End to end through the WRITER handler: the suite runs once, on the
    writer's tree, after the writer's session returned, and its record rides
    a ledger NOTE and the build state."""
    from app.factory.build import pytest_policy
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from tests.factory.test_codewhale_writer_dispatch import (
        _ctx,
        _plant_authored_handler,
        _receipt,
    )

    order: list = []
    roots: list = []

    def fake_run(prompt, dest, **kwargs):
        order.append("writer")
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    def spy(root, **kwargs):
        order.append("suite")
        roots.append(root)
        return {"outcome": outcome, "failures": failures, "elapsed_s": 1.5, "detail": ""}

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    monkeypatch.setattr(pytest_policy, "factory_suite_after_pass", spy)
    ctx = _ctx(tmp_path)
    notes: list = []
    ctx.progress = lambda detail, payload: notes.append((detail, payload))
    _plant_authored_handler(tmp_path / "build")

    assert _run_writer_via_codewhale_worker(ctx).ok is True
    assert order == ["writer", "suite"]
    assert [str(r) for r in roots] == [str(ctx.workspace.workspace)]
    suite_notes = [(d, p) for d, p in notes if p.get("reason") == "factory_suite"]
    assert len(suite_notes) == 1
    detail, payload = suite_notes[0]
    assert f"suite={outcome} failures={failures} " in detail
    assert payload["factory_suite"]["outcome"] == outcome
    assert ctx.state["factory_suite"]["failures"] == failures
