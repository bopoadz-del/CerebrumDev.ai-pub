"""F1 -- gate findings are typed objects; the ratchet reads ids, never text.

Owner order (F-series, prerequisite): GateResult.findings become
``{gate, check_id, capability_id|null, file, line|null, finding_shape,
detail}``; every gate emits them; the WRITER's work list and its rework
ratchet are built from ``capability_id`` fields, and the old text parse
(``roles_handlers._failing_capability_ids``) is gone. "A finding with no
capability_id never widens the ratchet; the writer receives ids only."

Capability ids here are invented; nothing names a real product.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build import findings as F
from app.factory.build.findings import Finding

BUILD = Path(__file__).resolve().parents[2] / "app" / "factory" / "build"


# -- the schema ----------------------------------------------------------------


def test_a_finding_carries_exactly_the_owner_schema_and_round_trips():
    f = Finding(
        "FAILED tests/test_x.py::test_y - refused",
        gate="suite_green",
        check_id="Gates",
        capability_id="zorblat_ledger",
        file="tests/test_x.py",
        line=12,
        finding_shape="Failure",
    )
    data = f.to_json()
    assert tuple(data) == F.FIELDS == (
        "gate", "check_id", "capability_id", "file", "line", "finding_shape", "detail",
    )
    assert data["check_id"] == "gates" and data["finding_shape"] == "failure"
    back = Finding.from_json(json.loads(json.dumps(data)))
    assert back.to_json() == data
    # The human line is the str value; decisions use the fields.
    assert str(f) == f.to_text() == "FAILED tests/test_x.py::test_y - refused"


def test_every_gate_result_holds_typed_findings_and_serialises_them():
    from app.factory.build.gates import GateResult

    v = GateResult(
        ok=False,
        gate="zorblat_gate",
        reason="zorblat_failed",
        findings=["one", "two"],
        payload={"finding_checks": ["check_a"]},
    )
    assert all(isinstance(x, Finding) for x in v.findings)
    assert [x.check_id for x in v.findings] == ["check_a", "zorblat_gate"]
    assert all(x.capability_id is None for x in v.findings)
    assert all(x.finding_shape == "zorblat_failed" for x in v.findings)
    out = v.to_json()
    assert out["findings"] == ["one", "two"]
    assert [r["check_id"] for r in out[F.TYPED_KEY]] == ["check_a", "zorblat_gate"]


def test_a_finding_without_a_named_check_takes_the_verdicts_check():
    """Regression: a Finding built with no check_id defaulted to its GATE as a
    *named* check, which outranked the verdict's brief check and sent a
    brief-defined failure to ADVISORY."""
    from app.factory.build import brief_gates
    from app.factory.build.gates import GateResult

    v = GateResult(
        ok=False,
        gate="zorblat_gate",
        findings=[Finding("x", gate="zorblat_gate", capability_id="kiln_queue")],
        payload={"check": "brief_check"},
    )
    assert v.findings[0].check_id == "brief_check"
    assert brief_gates.failure_checks(v)[0][0] == "brief_check"


def test_a_restamped_verdict_check_reaches_defaulted_findings_only():
    from app.factory.build import brief_gates
    from app.factory.build.gates import GateResult

    emitted = Finding("named", gate="g", check_id="own_check")
    v = GateResult(ok=False, gate="g", findings=["defaulted", emitted])
    stamped = brief_gates.declare_check(v, "brief_check")
    assert [x.check_id for x in stamped.findings] == ["brief_check", "own_check"]


# -- every gate emits them -------------------------------------------------------


def test_writer_gate_findings_take_the_capability_from_the_probe_record():
    from app.factory.build.writer_behaviour import (
        GATE_NAME,
        findings_from_probe_stderr,
        record_finding,
    )

    rec = {
        "gate_record": "miss",
        "kind": "f1",
        "text": "lantern_board: did not fail closed (F1)",
        "capability": "lantern_board",
    }
    f = record_finding(rec)
    assert (f.gate, f.check_id, f.capability_id, f.finding_shape) == (
        GATE_NAME, GATE_NAME, "lantern_board", "f1",
    )
    # A record that names no capability is not localised -- even when its
    # text happens to contain a capability id.
    bare = dict(rec, gate_record="finding")
    bare.pop("capability")
    halted = findings_from_probe_stderr(json.dumps(bare))
    assert [x.capability_id for x in halted] == [None]


def test_the_probe_writes_the_capability_as_a_record_field():
    from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE

    start = BEHAVIOUR_PROBE.index("class _Miss(str):")
    end = BEHAVIOUR_PROBE.index("def _halt(")
    import io
    import types

    buf = io.StringIO()
    ns: dict = {"json": json, "sys": types.SimpleNamespace(stdout=buf, stderr=buf)}
    exec(BEHAVIOUR_PROBE[start:end], ns)  # noqa: S102 -- the probe's own source
    ns["_record"]("miss", "f1", ns["_miss"]("kiln_queue", "kiln_queue: lied"))
    rec = json.loads(buf.getvalue())
    assert rec["capability"] == "kiln_queue" and rec["text"] == "kiln_queue: lied"


def _junit(tmp_path: Path) -> Path:
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_routes" name="test_every_route" '
        'file="tests/test_routes.py" line="3">'
        '<failure message="AssertionError: refused">trace</failure></testcase>'
        '<testcase classname="tests.test_health" name="test_boot" '
        'file="tests/test_health.py" line="1">'
        '<failure message="ImportError: no app">trace</failure></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return xml


def test_tester_findings_are_localised_by_the_suites_own_record(tmp_path):
    from app.factory.build.gates import _verdict_from_junit, failing_tests_from_junit

    xml = _junit(tmp_path)
    rows = failing_tests_from_junit(tmp_path, xml)
    routes = next(r["nodeid"] for r in rows if "test_routes" in r["nodeid"])
    log = Path(str(xml) + F.CAPABILITY_LOG_SUFFIX)
    log.write_text(
        json.dumps({"test": routes, "capability": "moth_register"}) + "\n"
        + json.dumps({"test": routes, "capability": "loom_tracker"}) + "\n",
        encoding="utf-8",
    )
    v = _verdict_from_junit(tmp_path, 1, xml, "", "suite_green")
    assert v is not None and not v.ok
    caps = sorted((x.capability_id or "") for x in v.findings)
    # One finding per recorded capability; the unrecorded failure stays
    # unlocalised (capability_id None) -- never guessed from its message.
    assert caps == ["", "loom_tracker", "moth_register"]
    assert {x.finding_shape for x in v.findings} == {"failure"}
    assert all(x.file for x in v.findings)


def test_the_emitted_recorder_writes_the_record_the_gate_reads(tmp_path):
    log = tmp_path / "caps.jsonl"
    src = "\n".join(F.render_capability_recorder()) + "\n_record_capability_failure('quarry_book')\n"
    env = dict(os.environ, **{F.CAPABILITY_LOG_ENV: str(log)},
               PYTEST_CURRENT_TEST="tests/test_q.py::test_q (call)")
    subprocess.run([sys.executable, "-c", src], env=env, check=True)
    assert F.read_capability_log(log) == {"tests/test_q.py::test_q": ["quarry_book"]}


def test_every_emitted_suite_carries_the_recorder():
    from app.factory.build.domain_acceptance import render_product_tests
    from app.factory.build.payload_helpers import render_payload_helpers

    helpers = "\n".join(render_payload_helpers())
    assert "_record_capability_failure(capability_id)" in helpers
    domain = render_product_tests({"tide_relay": {"fields": {}}})
    compile(domain, "test_domain_acceptance.py", "exec")
    assert "_record_capability_failure(" in domain
    handlers = (BUILD / "roles_handlers.py").read_text(encoding="utf-8")
    assert handlers.count("_record_capability_failure({cap.capability_id!r})") == 4


def test_store_gate_findings_name_the_failing_acceptance_check():
    from app.factory.build.gates import GateResult

    v = GateResult(
        ok=False,
        gate="store_gate",
        reason="store_gate_failed",
        findings=["metrics not mounted", "one persistence root unused"],
        payload={"check": "store_gate", "finding_checks": ["metrics_served", "single_persistence_root"]},
    )
    assert [x.check_id for x in v.findings] == ["metrics_served", "single_persistence_root"]
    assert all(x.capability_id is None for x in v.findings)


# -- the ratchet ---------------------------------------------------------------


CAPS = ["moth_register", "kiln_queue", "loom_tracker"]


def test_rule_no_work_list_means_every_capability():
    assert F.rework_targets((), CAPS) == set(CAPS)


def test_rule_named_capabilities_are_exactly_the_targets():
    work = [Finding("x", gate="g", capability_id="kiln_queue")]
    assert F.rework_targets(work, CAPS) == {"kiln_queue"}


def test_rule_an_unlocalised_finding_never_widens_the_ratchet():
    work = [
        Finding("x", gate="g", capability_id="kiln_queue"),
        Finding("infrastructure fell over", gate="g"),
        "[recheck] run the self-check",
    ]
    assert F.rework_targets(work, CAPS) == {"kiln_queue"}


def test_rule_nothing_localisable_regenerates_everything():
    work = [Finding("ImportError", gate="g"), "[recheck] run it"]
    assert F.rework_targets(work, CAPS) == set(CAPS)


def test_rule_text_naming_a_capability_localises_nothing():
    """The old parser found 'kiln_queue' in the text. Ids only now."""
    work = ["E  AssertionError: kiln_queue rejected a payload"]
    assert F.rework_targets(work, CAPS) == set(CAPS)


def test_a_capability_outside_the_plan_never_narrows_it():
    work = [Finding("x", gate="g", capability_id="not_in_plan")]
    assert F.rework_targets(work, CAPS) == set(CAPS)


# -- no text parsing anywhere ------------------------------------------------------


def _regex_over_finding_text(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "re"):
            continue
        for arg in ast.walk(ast.Module(body=[ast.Expr(a) for a in node.args], type_ignores=[])):
            name = arg.id if isinstance(arg, ast.Name) else (arg.attr if isinstance(arg, ast.Attribute) else "")
            if "work_list" in name or "finding" in name:
                bad.append(f"{path.name}:{node.lineno}")
    return bad


def test_runner_and_writer_never_run_a_regex_over_finding_text():
    for name in ("roles_handlers.py", "runner.py", "findings.py", "brief_gates.py"):
        assert _regex_over_finding_text(BUILD / name) == [], name
    assert "_failing_capability_ids" not in (BUILD / "roles_handlers.py").read_text(encoding="utf-8")


# -- through the runner and the ledger ----------------------------------------------


ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_the_writer_receives_typed_ids_and_the_ledger_round_trips_them(
    tmp_path, stub_coder, monkeypatch
):
    from app.factory.blueprint import load_blueprint
    from app.factory.build import brief_gates
    from app.factory.build import runner as runner_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateResult
    from app.factory.build.ledger import EventKind
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS
    from app.factory.build.runner import RoleRunner

    blueprint = load_blueprint(SMOKE)
    picked: dict = {}

    def verdict_for(ctx):
        # The blueprint's own first capability id (never a name we invent
        # into product code; the plan carries these same ids).
        cap = blueprint.capabilities[0].id
        picked["cap"] = cap
        picked["plan"] = [c.id for c in blueprint.capabilities]
        return GateResult(
            ok=False,
            gate="suite_green",
            reason="suite_red",
            detail="suite is red",
            findings=[
                Finding("FAILED tests/test_routes.py::test_r - refused", gate="suite_green",
                        capability_id=cap, finding_shape="failure"),
                Finding("FAILED tests/test_boot.py::test_b - boom", gate="suite_green",
                        finding_shape="failure"),
            ],
            payload={"check": brief_gates.SUITE_CHECK},
        )

    real = runner_mod.gate_for
    calls = {"n": 0}

    def gate_for(role):
        gate = real(role)
        if BuildRole(role) is not BuildRole.TESTER:
            return gate

        def tester(ctx):
            calls["n"] += 1
            return verdict_for(ctx) if calls["n"] == 1 else gate(ctx)

        return tester

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    seen: list = []
    real_writer = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        seen.append(tuple(ctx.work_list))
        return real_writer(ctx)

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    runner = RoleRunner(blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()
    assert outcome.ok, outcome.detail

    rework = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert len(rework) == 1
    payload = rework[0].payload
    cap = picked["cap"]
    assert payload["capability_ids"] == [cap]
    # The ledger round-trips the typed findings.
    back = F.from_payload(payload)
    assert [x.capability_id for x in back] == [cap, None]
    # The writer's rework round was handed typed items; the ratchet target
    # is the named capability only (the unlocalised one does not widen it).
    from app.factory.build.product_suites import finding_items, recheck_items

    handed = seen[-1]
    # Every FINDING the writer is handed is typed (ids, not text); the one
    # re-check command (#660, kind "recheck") is guidance, not a finding.
    found = finding_items(handed)
    assert found and all(isinstance(x, Finding) for x in found)
    assert [x.capability_id for x in found] == [cap, None]
    assert len(recheck_items(handed)) == 1
    assert F.rework_targets(handed, picked["plan"]) == {cap}
