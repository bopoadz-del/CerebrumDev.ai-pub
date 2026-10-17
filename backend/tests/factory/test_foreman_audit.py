"""F4 -- the foreman's audit pass: proposals after TESTER green, never a verdict.

Owner rule: after a TESTER green the foreman reads the workspace for intent
defects the gates cannot see and writes ONLY proposed gates (a structural rule
+ evidence) to artifacts/proposed_gates/<session>.json, shown on the Floor as
suggested checks for a human. It never decides a gate, never changes the work
list or the outcome, and its rules pass the same no-hardwiring lint as the
brief. ``FACTORY_FOREMAN_AUDIT`` = off | shadow | on.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build import architect
from app.factory.build import brief_gates
from app.factory.build import foreman as fm
from app.factory.build import runner as runner_mod
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.findings import Finding
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

CAP_A, CAP_B = "quillon_intake", "quillon_ledger"
CTX = fm.ForemanContext(
    capabilities=frozenset({CAP_A, CAP_B}),
    finding_capabilities=frozenset(),
    resolved_blocks=frozenset({"database"}),
    store_blocks=frozenset({"database"}),
    own_names=frozenset({"Quillon Works", CAP_A, CAP_B}),
    known_literals=frozenset(),
)
HANDLER = 'def handle(payload):\n    return {"ok": True}\n'
RULE = "a handler that answers ok:true writes the record it returned before answering"


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "app" / "handlers").mkdir(parents=True)
    (ws / "app" / "handlers" / f"{CAP_A}.py").write_text(HANDLER, encoding="utf-8")
    (ws / "app" / "handlers" / f"{CAP_B}.py").write_text(HANDLER, encoding="utf-8")
    return ws


def _gate(**over):
    gate = {
        "name": "handler_persists_what_it_accepts",
        "structural_rule": RULE,
        "evidence": [
            {"file": f"app/handlers/{CAP_A}.py", "line": 2, "excerpt": 'return {"ok": True}'},
        ],
    }
    gate.update(over)
    return gate


class _Llm:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _audit(llm, ws, tmp_path, *, mode="shadow", notes=None):
    return fm.audit_workspace(
        ws,
        ctx=CTX,
        note=(lambda d, **kw: notes.append((d, kw))) if notes is not None else None,
        llm=llm,
        mode=mode,
        session_id="sess_audit123",
        proposed_root=tmp_path,
    )


def _sink(tmp_path):
    return json.loads(tmp_path.joinpath(*fm.PROPOSED_GATES_DIR, "sess_audit123.json").read_text(encoding="utf-8"))


# -- the validator: structural rules, real evidence -----------------------------------------


def _errors(tmp_path, **over):
    ws = _workspace(tmp_path)
    gates, parse_errors = fm.parse_audit({"proposed_gates": [_gate(**over)]})
    assert not parse_errors, parse_errors
    return fm.validate_audit(gates, CTX, ws)


def test_a_structural_rule_with_real_evidence_passes(tmp_path):
    assert _errors(tmp_path) == []


def test_a_rule_naming_a_capability_is_refused(tmp_path):
    errs = _errors(tmp_path, structural_rule=f"{CAP_A} must persist what it accepts")
    assert any("must hold for every capability" in e for e in errs)


def test_a_rule_naming_the_product_is_refused(tmp_path):
    errs = _errors(tmp_path, name="quillon_check", structural_rule="Quillon Works handlers persist records")
    assert errs


def test_a_rule_naming_a_probe_id_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_probe_ids", lambda: frozenset({"P_zz9"}))
    errs = _errors(tmp_path, structural_rule="P_zz9 must answer with a stored record")
    assert any("probe or stage id" in e for e in errs)


def test_a_word_list_rule_is_refused(tmp_path):
    errs = _errors(tmp_path, structural_rule='a status is one of "a", "b" or "c"')
    assert any("word list" in e for e in errs)


def test_a_name_that_is_not_an_identifier_is_refused(tmp_path):
    assert any("short identifier" in e for e in _errors(tmp_path, name="persists what it accepts"))


def test_evidence_must_be_a_real_file_quoting_real_text(tmp_path):
    missing = _errors(tmp_path, evidence=[{"file": "app/nowhere.py", "line": None, "excerpt": "x"}])
    assert any("not a file in the workspace" in e for e in missing)
    misquoted = _errors(
        tmp_path / "b", evidence=[{"file": f"app/handlers/{CAP_A}.py", "line": 1, "excerpt": "persist(record)"}]
    )
    assert any("does not appear" in e for e in misquoted)
    past = _errors(tmp_path / "c", evidence=[{"file": f"app/handlers/{CAP_A}.py", "line": 99, "excerpt": "def handle"}])
    assert any("past the end" in e for e in past)
    escape = _errors(tmp_path / "d", evidence=[{"file": "../ws/app/x.py", "line": None, "excerpt": "x"}])
    assert any("not a file in the workspace" in e for e in escape)


# -- the entry point: sink, ledger, fallback, flag -------------------------------------------


def test_an_accepted_proposal_goes_to_the_sink_and_the_ledger(tmp_path):
    ws, notes = _workspace(tmp_path), []
    result = _audit(_Llm({"proposed_gates": [_gate()]}), ws, tmp_path, notes=notes)
    assert [g.name for g in result.gates] == ["handler_persists_what_it_accepts"] and not result.fallback
    rows = _sink(tmp_path)
    assert rows[0]["source"] == fm.AUDIT_ENTRY and rows[0]["evidence"][0]["file"].endswith(f"{CAP_A}.py")
    suggested = [kw for _d, kw in notes if fm.SUGGESTED_KEY in kw]
    assert suggested and suggested[0][fm.SUGGESTED_KEY][0]["name"] == "handler_persists_what_it_accepts"
    calls = [kw["architect"] for _d, kw in notes if "architect" in kw]
    assert calls[0]["entry"] == fm.AUDIT_ENTRY and calls[0]["mode"] == "shadow"


def test_the_sink_appends_and_dedupes_by_name(tmp_path):
    ws = _workspace(tmp_path)
    other = _gate(name="one_logic_one_name", structural_rule="two handlers with identical bodies are one capability")
    _audit(_Llm({"proposed_gates": [_gate()]}), ws, tmp_path)
    _audit(_Llm({"proposed_gates": [_gate(), other]}), ws, tmp_path)
    assert [r["name"] for r in _sink(tmp_path)] == ["handler_persists_what_it_accepts", "one_logic_one_name"]


def test_two_refusals_propose_nothing(tmp_path):
    ws, notes = _workspace(tmp_path), []
    bad = {"proposed_gates": [_gate(structural_rule=f"{CAP_A} persists")]}
    result = _audit(_Llm(bad, bad), ws, tmp_path, notes=notes)
    assert result.fallback and result.attempts == 2 and result.gates == ()
    assert not tmp_path.joinpath(*fm.PROPOSED_GATES_DIR).exists()
    assert not [kw for _d, kw in notes if fm.SUGGESTED_KEY in kw]


def test_a_refusal_is_regenerated_once(tmp_path):
    ws = _workspace(tmp_path)
    llm = _Llm({"proposed_gates": [_gate(structural_rule=f"{CAP_A} persists")]}, {"proposed_gates": [_gate()]})
    result = _audit(llm, ws, tmp_path)
    assert llm.calls == 2 and result.attempts == 2 and len(result.gates) == 1


def test_flag_off_makes_no_call(tmp_path, monkeypatch):
    monkeypatch.delenv(fm.AUDIT_FLAG_ENV, raising=False)
    llm = _Llm({"proposed_gates": [_gate()]})
    result = fm.audit_workspace(_workspace(tmp_path), ctx=CTX, llm=llm, proposed_root=tmp_path)
    assert result.mode == "off" and llm.calls == 0


def test_the_floor_reader_dedupes_and_drafts_a_floor_entry():
    class _E:
        def __init__(self, payload):
            self.payload = payload

    rows = [dict(_gate(), source="audit"), dict(_gate(), source="foreman")]
    out = fm.suggested_checks([_E({fm.SUGGESTED_KEY: rows}), _E({"other": 1})])
    assert len(out) == 1 and out[0]["source"] == "audit" and out[0]["evidence_count"] == 1
    entry = out[0]["floor_entry"]
    assert entry["id"] == "handler_persists_what_it_accepts" and entry["gate_fn"] is None
    assert entry["brief_render"] == f"- handler_persists_what_it_accepts: {RULE}"


# -- the call site in the runner: after TESTER green, never a verdict ----------------------------


def _run(tmp_path, monkeypatch, verdicts, *, audit, replies):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)
    monkeypatch.delenv(fm.FLAG_ENV, raising=False)
    monkeypatch.setenv(fm.AUDIT_FLAG_ENV, audit)
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "outputs"))
    llm = _Llm(*replies)
    monkeypatch.setattr(architect, "_default_llm", lambda: llm)
    queue = list(verdicts)
    calls = []

    def gate_for(role):
        role = BuildRole(role)

        def gate(ctx):
            if role is BuildRole.TESTER and queue:
                return queue.pop(0)
            return GateResult(ok=True, gate=f"{role.value.lower()}_gate", detail="pass")

        return gate

    def stub(role):
        def run(ctx):
            if role is BuildRole.WRITER:
                calls.append(tuple(str(w) for w in ctx.work_list))
            return RoleResult(ok=True, detail="stub")

        return run

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        roles={role: stub(role) for role in BUILD_PHASES},
        budget=BuildBudget(max_rework=2),
    )
    return runner, runner.run(), calls, llm


def _tester_fail(shape):
    cap = [c.id for c in load_blueprint(SMOKE).capabilities][0]
    finding = Finding(
        f"FAILED tests/test_routes.py::test_{shape} - refused",
        gate="TESTER",
        check_id=brief_gates.SUITE_CHECK,
        capability_id=cap,
        file="tests/test_routes.py",
        finding_shape=shape,
    )
    return GateResult(
        ok=False,
        gate="tester_gate",
        reason="scripted",
        detail="suite red",
        findings=[finding],
        payload={"check": brief_gates.SUITE_CHECK, "finding_checks": [brief_gates.SUITE_CHECK], "finding_shape": shape},
    )


def _audit_notes(runner):
    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and ((e.payload or {}).get("architect") or {}).get("entry") == fm.AUDIT_ENTRY
    ]


def test_runner_audits_once_after_tester_green_and_changes_nothing(tmp_path, monkeypatch):
    # The model proposes nothing it can evidence in a stub workspace -> an
    # empty proposal is a valid answer; what matters is when and how often.
    replies = [{"proposed_gates": []}]
    off, off_out, off_calls, off_llm = _run(tmp_path / "off", monkeypatch, [_tester_fail("a")], audit="off", replies=replies)
    on, on_out, on_calls, on_llm = _run(tmp_path / "on", monkeypatch, [_tester_fail("a")], audit="on", replies=replies)

    assert off_llm.calls == 0 and on_llm.calls == 1
    assert on_out.outcome == off_out.outcome and on_out.ok == off_out.ok
    assert on_calls == off_calls  # the work lists the writer got are identical

    events = list(on.ledger.events())
    audit_at = [i for i, e in enumerate(events) if e in _audit_notes(on)]
    tester_green = [
        i for i, e in enumerate(events) if e.kind is EventKind.GATE_PASSED and e.role is BuildRole.TESTER
    ]
    tester_red = [i for i, e in enumerate(events) if e.kind is EventKind.GATE_FAILED and e.role is BuildRole.TESTER]
    assert len(audit_at) == 1 and tester_green and tester_red
    assert audit_at[0] > tester_green[0] > tester_red[0]  # after green, never on the failure


def test_runner_never_audits_a_build_that_never_went_green(tmp_path, monkeypatch):
    fails = [_tester_fail("a"), _tester_fail("b"), _tester_fail("c")]
    runner, out, _calls, llm = _run(tmp_path, monkeypatch, fails, audit="on", replies=[{"proposed_gates": []}])
    assert not out.ok and llm.calls == 0 and not _audit_notes(runner)


def test_build_status_carries_the_suggested_checks(tmp_path, monkeypatch):
    from app.factory.build_jobs import build_status

    runner, out, _calls, _llm = _run(tmp_path, monkeypatch, [], audit="on", replies=[{"proposed_gates": []}])
    runner.ledger.append(
        EventKind.NOTE,
        role=BuildRole.TESTER,
        detail="suggested",
        payload={fm.SUGGESTED_KEY: [dict(_gate(), source=fm.AUDIT_ENTRY)]},
    )
    status = build_status(runner.workspace)
    names = [c["name"] for c in status["suggested_checks"]]
    assert names == ["handler_persists_what_it_accepts"]
    assert status["suggested_checks"][0]["floor_entry"]["gate_fn"] is None
