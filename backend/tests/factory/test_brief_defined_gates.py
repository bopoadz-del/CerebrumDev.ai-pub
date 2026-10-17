"""A gate exists only if a brief can turn it on (AGENTS.md, GATES.md).

A round's failures are classified brief-defined vs factory-invented BEFORE
any writer dispatch. A failure on a check the brief never defined is the
gate's fault, not the product's: it goes advisory with the reason in the
ledger, and no writer round is spent on it.

The invented gate id below is made up for this file. It is in no floor, no
brief and no gate, which is exactly the point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.product_suites import finding_items, recheck_items
from app.factory.blueprint import load_blueprint
from app.factory.build import brief_gates
from app.factory.build import runner as runner_mod
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

INVENTED = "zorblat_check"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def _events(runner, kind):
    return [e for e in runner.ledger.events() if e.kind is kind]


def _tester_gate_failing_once(monkeypatch, verdict):
    """The real gates, except the first TESTER verdict is ``verdict``."""
    real = runner_mod.gate_for
    calls = {"tester": 0}

    def gate_for(role):
        gate = real(role)
        if BuildRole(role) is not BuildRole.TESTER:
            return gate

        def tester(ctx):
            calls["tester"] += 1
            if calls["tester"] == 1:
                return verdict
            return gate(ctx)

        return tester

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    return calls


def _recording_writer(captured):
    real = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        captured.append(tuple(ctx.work_list))
        return real(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    return roles


# -- the classification itself ----------------------------------------------


def test_the_brief_defines_its_acceptance_checks_and_not_an_invented_one(blueprint):
    from app.factory.blocks_source import resolve_blocks_root
    from app.factory.planner import CapabilityPlanner

    root = resolve_blocks_root()
    root = Path(root) if root else None
    accept = brief_gates.compiled_acceptance_checks(
        blueprint, CapabilityPlanner(root).plan(blueprint), blocks_root=root
    )
    defined = brief_gates.brief_defined_checks(blueprint, accept)

    # the checks the TESTER verdicts declare are ones the compiled brief turns on
    assert brief_gates.SUITE_CHECK in defined
    assert brief_gates.PRODUCT_GATE_CHECK in defined
    # and so are the floor checks this brief enforces
    from app.factory.build.acceptance_floor import enforced_ids

    assert set(enforced_ids(blueprint)) <= defined
    assert INVENTED not in defined


def test_split_is_by_declared_id_never_by_finding_text():
    verdict = GateResult(
        ok=False,
        gate="suite_green",
        # the finding TEXT names a defined check; the declared id does not
        findings=["gates gates gates"],
        payload={"check": INVENTED},
    )
    split = brief_gates.split_failures(verdict, frozenset({brief_gates.SUITE_CHECK}))

    assert split.defined == ()
    assert split.invented_checks == [INVENTED]


def test_unjudged_round_trip_rows_are_never_classified():
    """A placeholder-connector capability is UNJUDGED, not failed: it sits in
    payload["unjudged"], never in findings, so it is neither handed to the
    writer nor recorded as advisory -- and narrowing keeps it as it was."""
    verdict = GateResult(
        ok=False,
        gate="product_green",
        findings=["orders: GET returned 0 record(s)"],
        payload={
            "check": "round_trip",
            "unjudged": ["ledger_sync: placeholder connector (503 unavailable)"],
        },
    )
    split = brief_gates.split_failures(verdict, frozenset())

    assert [f for _, f in split.invented] == ["orders: GET returned 0 record(s)"]
    assert split.defined == ()
    kept = brief_gates.narrowed(verdict, split)
    assert kept.payload["unjudged"] == verdict.payload["unjudged"]


# -- the runner ---------------------------------------------------------------


def test_an_invented_gate_goes_advisory_with_zero_writer_rounds(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _tester_gate_failing_once(
        monkeypatch,
        GateResult(
            ok=False,
            gate=INVENTED,
            reason="invented_failed",
            detail="a check no brief asked for",
            findings=["the factory's own idea of done"],
            payload={"check": INVENTED},
        ),
    )
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 0
    assert _events(runner, EventKind.REWORK) == [], "no writer round was spent"
    assert len(captured) == 1, "the writer ran once -- its first pass -- and never again"

    events = list(runner.ledger.events())
    advisory = next(
        i for i, e in enumerate(events)
        if e.kind is EventKind.NOTE and (e.payload or {}).get("gate_advisory")
    )
    record = events[advisory].payload
    assert record["gate_advisory"][0]["check"] == INVENTED
    assert record["gate_advisory"][0]["reason"] == brief_gates.REASON_NOT_DEFINED
    assert record["writer_dispatched"] is False
    assert not [
        e for e in events[advisory:]
        if e.kind is EventKind.PHASE_STARTED and e.role is BuildRole.WRITER
    ], "zero WRITER phases after the advisory"
    passed = next(
        e for e in events[advisory:]
        if e.kind is EventKind.GATE_PASSED and e.role is BuildRole.TESTER
    )
    assert passed.payload["advisory"] is True
    assert passed.payload["advisory_checks"] == [INVENTED]


def test_a_failing_check_the_compiled_brief_defines_reaches_the_writer(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    """The opposite of the invented gate: the brief DOES define this check,
    so the failure reaches the writer's work_list and a REWORK happens."""
    _tester_gate_failing_once(
        monkeypatch,
        GateResult(
            ok=False,
            gate="suite_green",
            reason="suite_red",
            detail="suite is red",
            findings=["FAILED tests/test_models.py::test_round_trip - boom"],
            payload={"check": brief_gates.SUITE_CHECK},
        ),
    )
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert brief_gates.SUITE_CHECK in runner._brief_defined_checks(), (
        "the compiled brief defines the failing check"
    )
    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 1
    assert len(_events(runner, EventKind.REWORK)) == 1
    # Typed work list: the FINDING items are exactly the brief-defined failure,
    # and the round carries the re-check command exactly once.
    assert finding_items(captured[-1]) == ("FAILED tests/test_models.py::test_round_trip - boom",)
    assert len(recheck_items(captured[-1])) == 1
    assert not [
        e for e in _events(runner, EventKind.NOTE) if (e.payload or {}).get("gate_advisory")
    ]


def test_mixed_round_hands_the_writer_only_the_brief_defined_failure(
    blueprint, tmp_path, stub_coder, monkeypatch
):
    _tester_gate_failing_once(
        monkeypatch,
        GateResult(
            ok=False,
            gate="suite_green",
            reason="suite_red",
            detail="two failures",
            findings=["invented finding", "defined finding"],
            payload={"finding_checks": [INVENTED, brief_gates.SUITE_CHECK]},
        ),
    )
    captured: list = []
    runner = RoleRunner(blueprint, tmp_path / "build", roles=_recording_writer(captured))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    assert outcome.rework_used == 1
    assert finding_items(captured[-1]) == ("defined finding",)
    assert len(recheck_items(captured[-1])) == 1
    rework = _events(runner, EventKind.REWORK)[0]
    assert rework.payload["findings"] == ["defined finding"]
    note = next(
        e for e in _events(runner, EventKind.NOTE) if (e.payload or {}).get("gate_advisory")
    )
    assert [a["check"] for a in note.payload["gate_advisory"]] == [INVENTED]
    assert note.payload["gate_advisory"][0]["findings"] == ["invented finding"]
    assert note.payload["writer_dispatched"] is True


def test_an_advisory_check_ships_in_the_status_and_the_exported_package(
    tmp_path, monkeypatch
):
    """Nothing is silenced out of sight: a check moved to advisory is listed,
    with its reason, in the build status the Floor reads AND in the
    MANIFEST.json inside the zip the package endpoint hands over."""
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from app.core.session_store import create_session, get_session, update_session
    from app.factory import build_jobs
    from app.factory.build.ledger import BuildLedger
    from app.main import app

    out = tmp_path / "advisory-product"
    (out / "app").mkdir(parents=True)
    (out / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    (out / "README.md").write_text("product", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="advisory-product", inputs_hash="abc")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.TESTER,
        detail="GATE ADVISORY",
        payload={
            "gate_advisory": [
                {
                    "check": INVENTED,
                    "reason": brief_gates.REASON_NOT_DEFINED,
                    "findings": ["one", "two"],
                }
            ],
            "writer_dispatched": False,
        },
    )

    expected = [
        {"check": INVENTED, "reason": brief_gates.REASON_NOT_DEFINED, "findings_count": 2,
         "findings": ["one", "two"]}
    ]
    # The status is read off the ledger -- the same answer every reader gets.
    real_status = build_jobs.build_status
    assert real_status(out)["advisory_checks"] == expected

    # Package eligibility (green CI, authored artifacts, acceptance) is not
    # this test's subject: grant it, keep the status's own advisory list.
    def eligible_status(output_dir, **kw):
        status = dict(real_status(output_dir, **kw))
        status.update(state="succeeded", pilot_ready=True, authorship={"agent_written": 1})
        return status

    monkeypatch.setattr(build_jobs, "build_status", eligible_status)
    monkeypatch.setattr(
        "app.factory.build.authorship.thin_store_green_export_blocker",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.factory.build.store_acceptance.acceptance_export_blocker",
        lambda *a, **k: None,
    )
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "ci-run-1")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")

    create_session("sess_advisory_pkg", "tester")
    state = get_session("sess_advisory_pkg")
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "advisory-product",
        "inputs_hash": "abc",
        "engine": "runner",
    }
    update_session("sess_advisory_pkg", state)

    pkg = TestClient(app).get("/v1/sessions/sess_advisory_pkg/product/package")
    assert pkg.status_code == 200, pkg.text
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(pkg.content)).read("MANIFEST.json"))
    assert manifest["advisory_checks"] == expected


def test_the_real_tester_verdict_declares_its_brief_check(tmp_path):
    from app.factory.build.gates import GateContext, gate_tester_contract

    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "app").mkdir()
    (ws / "app" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "tests" / "test_one.py").write_text("def test_x():\n    assert 1 == 2\n", encoding="utf-8")

    verdict = gate_tester_contract(GateContext(workspace=ws, role=BuildRole.TESTER))

    assert not verdict.ok
    assert verdict.payload["check"] == brief_gates.SUITE_CHECK


def test_the_old_standing_order_is_gone_and_the_new_one_is_written():
    old = "update GATES.md with any failure a gate missed"
    new = "A gate exists only if a brief can turn it on."
    for rel in ("AGENTS.md", "GATES.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert old not in text, rel
        assert new in " ".join(text.split()), rel
    for path in (ROOT / "prompts").glob("*.md"):
        assert old not in path.read_text(encoding="utf-8"), path.name
