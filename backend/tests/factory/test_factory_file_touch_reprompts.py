"""A writer pass that touches a Factory-owned file is re-prompted, never reworked.

Live, release cycle 8 (fintech repeat pick, sess_3436e978c9e543b7,
build/plt_227d00b5d7044aec): WRITER rounds 1/2 and 2/2 were both
``writer_authored_factory_file`` (the writer created TESTER's suites and
constraints.txt). The code cycle passed; the pilot cycle's first WRITER pass
then failed one real check and stopped at once: "WRITER gate rework budget of
2 spent". The product never heard its finding -- its budget had gone on files
it may not write.

Owner rule: a writer that burns its rework budget on a Factory-owned file is a
FACTORY defect, never charged to the product. The WRITER gate rejects the pass
on the first such write (the Factory's versions are put back) and the writer
is re-prompted with the paths, at no rework cost. A writer that keeps doing it
after being told is a FACTORY stop.

The reasons and file names below are made up for this file.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build import brief_gates, factory_owned
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext, GateResult, gate_writer_contract
from app.factory.build.ledger import EventKind

#: The WRITER gate's reason for such a pass.
TOUCHED = "writer_authored_factory_file"

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _touched(tmp_path: Path, name: str, rel: str) -> GateResult:
    root = tmp_path / name
    factory_owned.record(root, [{"path": rel, "change": "created"}])
    verdict = gate_writer_contract(GateContext(workspace=root, role=BuildRole.WRITER))
    assert verdict.reason == TOUCHED
    return verdict


def _product_finding(reason: str, finding: str) -> GateResult:
    return GateResult(
        ok=False,
        gate=brief_gates.WRITER_CONTRACT_CHECK,
        reason=reason,
        detail=f"{reason}: {finding}",
        findings=[finding],
    )


def _runner(tmp_path):
    from app.factory.build.runner import RoleRunner

    return RoleRunner(load_blueprint(SMOKE), tmp_path / "build")


def _decide(runner, verdict, reprompts):
    """``RoleRunner.decide`` for one WRITER verdict, as the run loop calls it."""
    import inspect

    kwargs = {"rework_used": runner._rework_rounds_this_build()}
    if "factory_file_reprompts" in inspect.signature(runner.decide).parameters:
        kwargs["factory_file_reprompts"] = reprompts
    return runner.decide(BuildRole.WRITER, verdict, **kwargs)


def _drive(runner, verdicts):
    """Feed verdicts the way the run loop does; return each decision."""
    from app.factory.build.rule_decision import STOP

    decisions, reprompts = [], 0
    for verdict in verdicts:
        decision = _decide(runner, verdict, reprompts)
        decisions.append(decision)
        if decision.kind == STOP:
            break
        if decision.kind == "REPROMPT":
            reprompts += 1
    return decisions


def test_touching_factory_files_spends_no_rework_budget(tmp_path):
    runner = _runner(tmp_path)
    decisions = _drive(runner, [
        _touched(tmp_path, "a", "tests/test_domain_acceptance.py"),
        _touched(tmp_path, "b", "constraints.txt"),
        _product_finding("quillfeather_first", "app/zorblat.py:3: the first real finding"),
        _product_finding("quillfeather_second", "app/zorblat.py:9: the second real finding"),
    ])
    kinds = [d.kind for d in decisions]
    assert kinds == ["REPROMPT", "REPROMPT", "REWORK", "REWORK"], [(d.kind, d.detail) for d in decisions]
    reworks = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert not [e for e in reworks if TOUCHED in (e.detail or "")]
    # Both real findings were heard, each in its own rework round.
    assert any("the first real finding" in e.detail for e in reworks)
    assert any("the second real finding" in e.detail for e in reworks)


def test_the_reprompt_names_the_touched_files(tmp_path):
    runner = _runner(tmp_path)
    (decision,) = _drive(runner, [_touched(tmp_path, "a", "tests/test_domain_acceptance.py")])
    assert decision.kind == "REPROMPT"
    assert any("tests/test_domain_acceptance.py" in item for item in decision.work_list), decision.work_list


def test_a_writer_that_keeps_touching_them_is_a_factory_stop(tmp_path):
    runner = _runner(tmp_path)
    decisions = _drive(runner, [
        _touched(tmp_path, "a", "tests/test_domain_acceptance.py"),
        _touched(tmp_path, "b", "constraints.txt"),
        _touched(tmp_path, "c", "conftest.py"),
    ])
    assert [d.kind for d in decisions] == ["REPROMPT", "REPROMPT", "STOP"], [d.detail for d in decisions]
    assert "FACTORY_FAULT" in decisions[-1].detail and "conftest.py" in decisions[-1].detail
    assert not [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]


def test_the_run_loop_counts_reprompts_apart_from_rework():
    import inspect

    from app.factory.build.runner import RoleRunner

    src = inspect.getsource(RoleRunner.run)
    assert "factory_file_reprompts=factory_file_reprompts" in src
    assert "factory_file_reprompts += 1" in src
