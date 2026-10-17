"""Code-cycle SUCCESS is not the end when a factory coder key is set.

The residential-lettings Floor run wrote RUN_SUCCEEDED after ~14 min with
pilot_ready=false and the UI said Finished / Download ready. These tests
pin the two halves of the fix:

1. Auto-open a pilot cycle on the same workspace when the factory LLM is
   configured (or FACTORY_AUTO_PILOT=1), without writing a code SUCCESS.
2. Status / export copy stays honest when a code-only SUCCESS still lands
   (no key, explicit FACTORY_AUTO_PILOT=0).
"""

from __future__ import annotations

from app.factory.build.runner import REWORK_BUDGET

import zipfile
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.auto_pilot import (
    AUTO_PILOT_CEILING_S,
    AUTO_PILOT_STAGE_2_S,
    AUTO_PILOT_WALL_CLOCK_S,
    factory_auto_pilot_enabled,
    factory_llm_ready,
)
from app.factory.build.budget_inspect import STAGE_1_S, STAGE_2_S
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build.roles_handlers import _writer_block_roster
from app.factory.build_jobs import (
    _max_rework,
    _phase_wall_clock_s,
    _wall_clock_s,
    build_status,
)
from app.routers.session_product import _PROTOTYPE_MARKER, zip_generated_product

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)
    monkeypatch.delenv("KEYED_PATH_CI", raising=False)
    for var in (
        "KIMI_API_KEY",
        "CEREBRUM_LLM_API_KEY",
        "CEREBRUM_FACTORY_LLM_API_KEY",
        "KIMI_MOCK",
        "CEREBRUM_LLM_MOCK",
    ):
        monkeypatch.delenv(var, raising=False)


def test_writer_block_roster_stays_the_pass_list_not_the_shelf():
    """Preconditions must not expand to vendor_blocks_mirror / the factory shelf."""
    assert _writer_block_roster({"vendored_blocks": ("team", "storage")}) == (
        "storage",
        "team",
    )
    assert _writer_block_roster({"vendored_blocks": {"team": "x", "storage": "y"}}) == (
        "storage",
        "team",
    )
    assert _writer_block_roster({}) == ()


def test_auto_pilot_off_without_a_factory_key():
    assert factory_llm_ready() is False
    assert factory_auto_pilot_enabled() is False


def test_auto_pilot_on_when_factory_key_is_set(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("KIMI_API_KEY", "sk-live-not-used")
    assert factory_llm_ready() is True
    assert factory_auto_pilot_enabled() is True


def test_auto_pilot_explicit_off_wins_even_with_a_key(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("KIMI_API_KEY", "sk-live-not-used")
    monkeypatch.setenv("FACTORY_AUTO_PILOT", "0")
    assert factory_auto_pilot_enabled() is False


def test_keyed_path_ci_does_not_auto_open_pilot(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("KIMI_API_KEY", "ci-stub-not-a-real-key")
    monkeypatch.setenv("KEYED_PATH_CI", "1")
    assert factory_auto_pilot_enabled() is False


def test_floor_budget_stays_a_code_phase_without_auto_pilot(monkeypatch):
    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_BUILD_MAX_REWORK", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    assert _wall_clock_s() == 1800.0
    # Owner rule: 2 rework rounds per gate, at every cycle.
    assert _max_rework() == REWORK_BUDGET == 2
    assert _phase_wall_clock_s() == 1500.0


def test_floor_budget_starts_stage_1_not_a_silent_2h(monkeypatch):
    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_BUILD_MAX_REWORK", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    assert AUTO_PILOT_WALL_CLOCK_S == STAGE_1_S == 1800.0
    assert AUTO_PILOT_STAGE_2_S == STAGE_2_S == 2700.0
    assert AUTO_PILOT_CEILING_S == 7200.0
    assert _wall_clock_s(auto_pilot=True) == AUTO_PILOT_WALL_CLOCK_S
    assert _wall_clock_s(auto_pilot=True) != AUTO_PILOT_CEILING_S
    assert _max_rework(auto_pilot=True) == REWORK_BUDGET
    assert _phase_wall_clock_s(auto_pilot=True) == 5400.0
    assert _wall_clock_s(cycle="pilot") == AUTO_PILOT_WALL_CLOCK_S
    assert _max_rework(cycle="pilot") == REWORK_BUDGET
    assert _phase_wall_clock_s(cycle="pilot") == 5400.0


def test_leftover_2h_build_wall_is_honoured_not_slashed(monkeypatch):
    monkeypatch.setenv("FACTORY_BUILD_WALL_CLOCK_S", "7200")
    assert _wall_clock_s(auto_pilot=True) == 7200.0
    assert _wall_clock_s() == 7200.0


def test_legacy_25min_phase_wall_does_not_cap_a_pilot_writer(monkeypatch):
    """Dashboard leftover FACTORY_PHASE_WALL_CLOCK_S=1500 is the code-only
    cap. A Store-green WRITER must keep the 90-minute coding window.
    """
    monkeypatch.setenv("FACTORY_PHASE_WALL_CLOCK_S", "1500")
    assert _phase_wall_clock_s() == 1500.0
    assert _phase_wall_clock_s(auto_pilot=True) == 5400.0
    assert _phase_wall_clock_s(cycle="pilot") == 5400.0
    monkeypatch.setenv("FACTORY_PHASE_WALL_CLOCK_S", "0")
    assert _phase_wall_clock_s(cycle="pilot") == 0.0
    monkeypatch.setenv("FACTORY_PHASE_WALL_CLOCK_S", "6600")
    assert _phase_wall_clock_s(cycle="pilot") == 6600.0


def test_build_status_exposes_code_cycle_as_not_pilot_ready(tmp_path):
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="lettings", inputs_hash="abc")
    for role in BuildRole:
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS",
        payload={"cycle": "code", "pilot_ready": False, "rework_used": 0},
    )
    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["cycle"] == "code"
    assert status["pilot_ready"] is False
    assert status["auto_pilot"] is False


def test_zip_stamps_a_code_cycle_prototype_marker(tmp_path):
    out = tmp_path / "build"
    out.mkdir()
    (out / "README.md").write_text("thin", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="lettings", inputs_hash="abc")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS",
        payload={"cycle": "code", "pilot_ready": False},
    )
    archive = zip_generated_product(out, tmp_path / "export")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert _PROTOTYPE_MARKER in names
        body = zf.read(_PROTOTYPE_MARKER).decode("utf-8")
    assert "CODE-CYCLE PROTOTYPE" in body
    assert "pilot_ready=false" in body


def test_zip_omits_prototype_marker_when_pilot_ready(tmp_path):
    out = tmp_path / "build"
    out.mkdir()
    (out / "README.md").write_text("green", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="lettings", inputs_hash="abc")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="STORE PASS",
        payload={"cycle": "pilot", "pilot_ready": True},
    )
    archive = zip_generated_product(out, tmp_path / "export")
    with zipfile.ZipFile(archive) as zf:
        assert _PROTOTYPE_MARKER not in zf.namelist()


def test_auto_pilot_opens_pilot_instead_of_code_success(tmp_path, monkeypatch, stub_coder):
    """A keyed (or forced) run must not park on code-cycle SUCCESS.

    The smoke product may fail Store-green; the contract is that the ledger
    never records a terminal code SUCCESS when auto-pilot is on.

    0.5: the code cycle must reach SUCCESS for the pilot to open at all,
    and an un-stubbed writer refuses (writer_no_output); stub a
    deterministic coding agent so this test exercises auto-pilot itself.
    """
    monkeypatch.setenv("FACTORY_AUTO_PILOT", "1")
    out = tmp_path / "build"
    runner = RoleRunner(
        load_blueprint(SMOKE),
        out,
        budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=300),
        auto_pilot=True,
    )
    outcome = runner.run()
    kinds = [e.kind for e in runner.ledger.events()]
    assert EventKind.PILOT_OPENED in kinds
    terminal = runner.ledger.terminal_event()
    if terminal is not None and terminal.kind is EventKind.RUN_SUCCEEDED:
        assert runner.ledger.pilot_ready() is True
        assert (terminal.payload or {}).get("cycle") == "pilot"
    else:
        assert runner.ledger.pilot_ready() is False
        assert outcome.ok is False
        # Never a silent code-only SUCCESS that the Floor would oversell.
        successes = [
            e
            for e in runner.ledger.events()
            if e.kind is EventKind.RUN_SUCCEEDED
        ]
        assert successes == []


def test_floor_run_stays_code_only_without_auto_pilot(monkeypatch, tmp_path):
    captured = {}

    class _FakeRunner:
        def __init__(self, *a, **k):
            captured["auto_pilot"] = k.get("auto_pilot")
            captured["budget"] = k.get("budget")

        def run(self):
            from app.factory.build.runner import BuildOutcome, Outcome

            return BuildOutcome(outcome=Outcome.SUCCESS)

    monkeypatch.setenv("FACTORY_AUTO_PILOT", "0")
    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_BUILD_MAX_REWORK", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _FakeRunner)
    from app.factory.build_jobs import _run

    out = tmp_path / "out"
    out.mkdir()
    _run(load_blueprint(SMOKE), out, None, "code")
    assert captured["auto_pilot"] is False
    assert captured["budget"].wall_clock_s == 1800.0
    assert captured["budget"].max_rework == REWORK_BUDGET == 2
    assert captured["budget"].phase_wall_clock_s == 1500.0


def test_floor_run_opts_into_auto_pilot_when_enabled(monkeypatch, tmp_path):
    """The Floor thread is the production door — it must pass auto_pilot."""
    captured = {}

    class _FakeRunner:
        def __init__(self, *a, **k):
            captured["auto_pilot"] = k.get("auto_pilot")
            captured["budget"] = k.get("budget")
            captured["cycle"] = k.get("cycle")

        def run(self):
            from app.factory.build.runner import BuildOutcome, Outcome

            return BuildOutcome(outcome=Outcome.SUCCESS)

    monkeypatch.setenv("FACTORY_AUTO_PILOT", "1")
    monkeypatch.delenv("FACTORY_BUILD_WALL_CLOCK_S", raising=False)
    monkeypatch.delenv("FACTORY_BUILD_MAX_REWORK", raising=False)
    monkeypatch.delenv("FACTORY_PHASE_WALL_CLOCK_S", raising=False)
    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _FakeRunner)
    from app.factory.build_jobs import _run

    out = tmp_path / "out"
    out.mkdir()
    _run(load_blueprint(SMOKE), out, None, "code")
    assert captured["auto_pilot"] is True
    assert captured["cycle"] == "code"
    assert captured["budget"].wall_clock_s == AUTO_PILOT_WALL_CLOCK_S
    assert captured["budget"].max_rework == REWORK_BUDGET
    assert captured["budget"].phase_wall_clock_s == 5400.0


def test_code_only_success_still_exists_without_auto_pilot(tmp_path, stub_coder):
    # 0.5: a green build needs a coding agent; stub a deterministic one.
    out = tmp_path / "build"
    runner = RoleRunner(
        load_blueprint(SMOKE),
        out,
        auto_pilot=False,
    )
    outcome = runner.run()
    assert outcome.ok, outcome.to_dict()
    assert runner.ledger.succeeded()
    assert runner.ledger.pilot_ready() is False
    assert (runner.ledger.terminal_event().payload or {}).get("cycle") == "code"
    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is False
    assert status["cycle"] == "code"
    assert status["auto_pilot"] is False


def test_build_status_exposes_auto_pilot_when_keyed(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("KIMI_API_KEY", "sk-live-not-used")
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="lettings", inputs_hash="abc")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        detail="CODE PASS",
        payload={"cycle": "code", "pilot_ready": False},
    )
    status = build_status(out)
    assert status["pilot_ready"] is False
    assert status["auto_pilot"] is True
