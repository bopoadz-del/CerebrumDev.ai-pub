"""FACTORY_GATE_COLLECT_ALL: one instrumented run, every finding, no halt.

Board P7: serial halt-close peels surfaced one finding per Approve run.
Collect-all suppresses gate halts so a single run logs the complete list,
then ends as COLLECT_ALL_REPORT — recorded, not ok, no sealed identity —
never a plausible SUCCESS.
"""

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.ledger import EventKind
from app.factory.build.authority import BuildRole
from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult
from app.factory.build.runner import Outcome, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def test_collect_all_records_failed_gate_and_keeps_going(
    blueprint, tmp_path, monkeypatch, stub_coder
):
    """A gate failure that is terminal today becomes a recorded finding."""
    monkeypatch.setenv("FACTORY_GATE_COLLECT_ALL", "1")

    def empty_cloner(ctx):
        # Claims blocks were vendored but writes nothing (cloner gate fails).
        return RoleResult(ok=True, detail="lied", vendored_blocks=("analytics",))

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.CLONER] = empty_cloner

    runner = RoleRunner(blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()

    assert outcome.outcome is Outcome.COLLECT_ALL_REPORT
    assert not outcome.ok, "an instrument report must never read as success"
    assert outcome.findings, "the suppressed gate's findings must be collected"
    assert any("cloner" in f.lower() for f in outcome.findings)
    notes = [e for e in runner.ledger.events() if e.kind is EventKind.NOTE]
    assert any("COLLECT-ALL" in (e.detail or "") for e in notes)
    assert outcome.rework_used == 0, "collect-all is one linear pass, no rework"
    # The run went PAST the failed phase instead of stopping at it: later
    # phases are ledger-completed. The suppressed phase itself must NOT be
    # recorded as completed (its gate never passed); the ledger stays honest.
    assert BuildRole.STORE_MANAGER in outcome.completed
    assert BuildRole.CLONER not in outcome.completed


def test_collect_all_clean_run_is_still_success(blueprint, tmp_path, monkeypatch, stub_coder):
    """The flag alone must not change a green build's outcome."""
    monkeypatch.setenv("FACTORY_GATE_COLLECT_ALL", "1")
    outcome = RoleRunner(blueprint, tmp_path / "build").run()
    assert outcome.outcome is Outcome.SUCCESS
    assert outcome.ok


def test_flag_off_keeps_terminal_behaviour(blueprint, tmp_path, monkeypatch):
    """Default (flag unset) is byte-for-byte today's halt semantics."""
    monkeypatch.delenv("FACTORY_GATE_COLLECT_ALL", raising=False)

    def empty_cloner(ctx):
        return RoleResult(ok=True, detail="lied", vendored_blocks=("analytics",))

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.CLONER] = empty_cloner

    outcome = RoleRunner(blueprint, tmp_path / "build", roles=roles).run()
    assert outcome.outcome is Outcome.FAILED_GATE
    assert outcome.failed_phase is BuildRole.CLONER
