"""Writer-side internal audit: the F-8 grader runs on the writer's tree
BEFORE the TESTER judges it.

Nothing in this file names a real product, client, vertical or capability:
the fixtures are invented (the same shape test_product_function_grader.py
uses) and the brief text is generic prose.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build import internal_audit as ia
from app.factory.build.product_grader import grade_record, layer2

READS_INPUT = (
    "def handle(payload):\n"
    "    return {'ok': True, 'echo': sorted(payload.items())}\n"
)
CONSTANT = "def handle(payload):\n    return {'ok': True}\n"

IDS = ["intake_form", "invoice_total", "visit_log", "tenant_notice"]


def _workspace(tmp_path: Path, bodies: dict, extra: dict = None) -> Path:
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    for cid, body in bodies.items():
        (actions / f"{cid}.py").write_text(body)
    for rel, text in (extra or {}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _bp(ids, ui_modules=()):
    return {
        "capabilities": [{"id": i, "description": f"record the {i} entry with owner and amount"} for i in ids],
        "ui_modules": list(ui_modules),
    }


def _passing_l2(blueprint):
    """A Layer-2 completion that PASSes every generated question, quoting
    evidence -- built from the blueprint, never hand-answered."""

    def complete(messages):
        questions = layer2.layer2_questions(blueprint)
        return json.dumps(
            [
                {"question": q, "verdict": "PASS", "evidence": "the product delivers this (quoted)"}
                for q in questions
            ]
        )

    return complete


# --- derivation (no hardwiring) ---------------------------------------------


def test_derive_forbidden_takes_brief_shapes_not_prose():
    brief = (
        'The client "Acme Trading Co" needs 240 invoices and a "Nimbus" portal. '
        "Plain lowercase prose words are not fixture-shaped."
    )
    forbidden = ia.derive_forbidden({}, brief, "")
    assert "Acme Trading Co" in forbidden
    assert "Nimbus" in forbidden
    assert "240" in forbidden
    assert "prose" not in forbidden
    assert "lowercase" not in forbidden


def test_derive_forbidden_reads_structured_blueprint_values():
    bp = {"client": "Blue Harbor Estates", "vertical": "property"}
    forbidden = ia.derive_forbidden(bp, "some brief", "")
    assert "Blue Harbor Estates" in forbidden
    assert "property" in forbidden


def test_derive_forbidden_takes_document_references_and_file_shapes():
    brief = "See document:handover_notes.docx and the rates_sheet.xlsx values."
    forbidden = ia.derive_forbidden({}, brief, "")
    assert "handover_notes.docx" in forbidden
    assert "handover_notes" in forbidden
    assert "rates_sheet.xlsx" in forbidden


def test_render_product_includes_app_and_frontend_only_bounded():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _workspace(root, {i: READS_INPUT for i in IDS[:2]}, extra={
            "frontend/src/app.tsx": "export const App = () => null;",
            ".venv/x.py": "SECRET = 'should never appear'",
        })
        rendered = ia.render_product(root)
        assert "app/actions/intake_form.py" in rendered
        assert "frontend/src/app.tsx" in rendered
        assert "should never appear" not in rendered


# --- the audit itself --------------------------------------------------------


def test_green_audit_writes_the_grade_and_returns_no_refusal(tmp_path):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS)
    result = ia.run_internal_audit(
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        compiled_brief={"summary": "a generic brief about entries and amounts"},
        complete_l2=_passing_l2(bp),
    )
    assert result.unavailable is None
    assert result.writer_refusal is None
    assert (ws / "grade.json").is_file()
    assert (ws / "GRADE.md").is_file()
    grade = json.loads((ws / "grade.json").read_text("utf-8"))
    assert grade["layer1"]["functions"]["ok"] is True
    assert grade["layer2"]["ran"] is True
    # UI could not run here (no booted product): recorded, never fabricated.
    assert grade["layer1"]["ui"]["ok"] is False
    assert grade["certified"] is False
    assert grade_record.certification_refusal(ws) is not None


def test_red_function_grade_returns_writer_findings_with_derived_checks(tmp_path):
    ws = _workspace(tmp_path, {i: (CONSTANT if i == "invoice_total" else READS_INPUT) for i in IDS})
    bp = _bp(IDS)
    result = ia.run_internal_audit(
        blueprint=bp, workspace=ws, product_id="invented-product", complete_l2=_passing_l2(bp)
    )
    assert result.unavailable is None
    assert result.writer_refusal is not None
    assert "function grade FAIL: invoice_total" in result.findings
    assert "audit.function.invoice_total" in result.checks
    # every finding has its own derived check (distinct failure keys)
    assert len(result.checks) == len(result.findings)
    assert len(set(result.checks)) == len(result.checks)


def test_layer2_that_never_answers_is_an_outage_not_writer_work(tmp_path):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS)
    result = ia.run_internal_audit(
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        complete_l2=lambda messages: "not json",
    )
    assert result.writer_refusal is None
    assert result.unavailable is not None
    assert "Layer 2 did not run" in result.unavailable
    # the red record still lands: an unaudited build is never a silent pass
    assert (ws / "grade.json").is_file()
    assert grade_record.certification_refusal(ws) is not None


def test_function_grade_crash_is_an_outage_and_still_writes_a_red_record(tmp_path, monkeypatch):
    from app.factory.build.product_grader import function_grader as fg

    def boom(*args, **kwargs):
        raise RuntimeError("grader exploded")

    monkeypatch.setattr(fg, "grade_functions", boom)
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    result = ia.run_internal_audit(
        blueprint=_bp(IDS), workspace=ws, product_id="invented-product", complete_l2=_passing_l2(_bp(IDS))
    )
    assert result.writer_refusal is None
    assert result.unavailable is not None
    assert "grader exploded" in result.unavailable
    assert (ws / "grade.json").is_file()
    assert grade_record.certification_refusal(ws) is not None


def test_hardwired_brief_literal_is_found_and_turned_into_writer_work(tmp_path):
    brief = 'The client "Acme Trading Co" runs 240 invoices through the portal.'
    ws = _workspace(
        tmp_path,
        {i: READS_INPUT for i in IDS},
        extra={"app/constants.py": 'CLIENT = "Acme Trading Co"\n'},
    )
    bp = _bp(IDS)
    result = ia.run_internal_audit(
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        compiled_brief={"summary": brief},
        prompt_text="",
        complete_l2=_passing_l2(bp),
    )
    assert result.writer_refusal is not None
    assert any("Acme Trading Co" in finding for finding in result.findings)
    assert any(str(t).startswith("remove baked literals") for t in result.findings)
    assert any(c.startswith("audit.forbidden.") for c in result.checks)


def test_prompt_leak_is_found_when_the_literal_sits_in_both(tmp_path):
    brief = "The project is code-named Nimbus for its portal."
    prompt = "Build the portal for the Nimbus project."
    ws = _workspace(
        tmp_path,
        {i: READS_INPUT for i in IDS},
        extra={"app/settings.py": 'PROJECT = "Nimbus"\n'},
    )
    bp = _bp(IDS)
    result = ia.run_internal_audit(
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        compiled_brief={"summary": brief},
        prompt_text=prompt,
        complete_l2=_passing_l2(bp),
    )
    assert result.writer_refusal is not None
    assert any(str(t).startswith("stop leaking the prompt into the source") for t in result.findings)


# --- the runner wiring -------------------------------------------------------


@pytest.fixture()
def smoke_blueprint():
    from app.factory.blueprint import load_blueprint

    return load_blueprint(
        Path(__file__).resolve().parents[3] / "blueprints" / "examples" / "runner_smoke.yaml"
    )


def _red_product(workspace: Path) -> None:
    actions = workspace / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (workspace / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    for cid in ("analytics_surface", "dashboard_surface"):
        (actions / f"{cid}.py").write_text(CONSTANT)


def test_runner_audit_returns_a_failed_verdict_for_a_red_tree(smoke_blueprint, tmp_path, monkeypatch):
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(ia.DISABLE_ENV, raising=False)
    workspace = tmp_path / "w"
    workspace.mkdir()
    _red_product(workspace)
    runner = RoleRunner(smoke_blueprint, workspace)
    monkeypatch.setattr(ia, "_complete_via_chat_config", _passing_l2(_bp(IDS)))
    outcome = runner._writer_internal_audit()
    assert outcome is not None
    assert outcome.outage is None
    assert outcome.verdict is not None
    assert outcome.verdict.gate == ia.AUDIT_GATE
    assert outcome.verdict.ok is False
    assert outcome.verdict.reason == "grade_red"
    assert outcome.verdict.findings
    # per-finding derived checks ride in the payload for same-failure keys
    checks = outcome.verdict.payload.get("finding_checks") or []
    assert len(checks) == len(outcome.verdict.findings)
    # the audit is a mandatory gate: it never routes as advisory
    assert runner._split_by_brief(outcome.verdict) is None


def test_runner_audit_is_none_when_the_switch_is_off(smoke_blueprint, tmp_path, monkeypatch):
    from app.factory.build.runner import RoleRunner

    workspace = tmp_path / "w"
    workspace.mkdir()
    _red_product(workspace)
    runner = RoleRunner(smoke_blueprint, workspace)
    monkeypatch.setenv(ia.DISABLE_ENV, "0")
    assert runner._writer_internal_audit() is None


# --- the loop integration -----------------------------------------------------


def _outage_audit(**kwargs):
    return ia.InternalAuditResult(grade={}, writer_refusal=None, unavailable="grader could not run (test)")


def _red_audit(**kwargs):
    return ia.InternalAuditResult(
        grade={},
        writer_refusal="audit RED (test)",
        unavailable=None,
        findings=("function grade FAIL: zorblat_check",),
        checks=("audit.function.zorblat_check",),
    )


def _fake_pass_harness(monkeypatch):
    """Every phase role passes instantly and every phase gate is green, so
    the run reaches the writer-side audit hook and nothing else decides."""
    from app.factory.build import runner as runner_mod
    from app.factory.build.gates import GateResult
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult

    roles = {role: (lambda ctx: RoleResult(ok=True, detail="fake pass")) for role in ROLE_IMPLEMENTATIONS}
    monkeypatch.setattr(runner_mod, "gate_for", lambda role: (lambda ctx: GateResult(ok=True, gate="fake_gate")))
    return roles


def test_a_loop_run_stops_when_the_audit_cannot_run(smoke_blueprint, tmp_path, monkeypatch):
    """An audit outage is terminal: the run names it and stops -- the
    writer is never sent back on a phantom rework."""
    from app.factory.build.runner import Outcome, RoleRunner

    monkeypatch.delenv(ia.DISABLE_ENV, raising=False)
    monkeypatch.setattr(ia, "run_internal_audit", _outage_audit)
    roles = _fake_pass_harness(monkeypatch)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()
    assert not outcome.ok
    assert outcome.outcome is Outcome.FAILED_GATE
    assert outcome.detail.startswith("internal audit unavailable")


def test_a_loop_run_reworks_the_writer_when_the_audit_is_red(smoke_blueprint, tmp_path, monkeypatch):
    """A red audit sends the writer back through the normal rework path,
    and the same red twice stops by the same-failure-twice rule."""
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(ia.DISABLE_ENV, raising=False)
    monkeypatch.setattr(ia, "run_internal_audit", _red_audit)
    roles = _fake_pass_harness(monkeypatch)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()
    assert not outcome.ok
    reworks = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert len(reworks) == 1
    assert reworks[0].payload["gate"] == ia.AUDIT_GATE
    assert reworks[0].payload["source"] == "WRITER"
    assert "function grade FAIL: zorblat_check" in reworks[0].payload["findings"]
