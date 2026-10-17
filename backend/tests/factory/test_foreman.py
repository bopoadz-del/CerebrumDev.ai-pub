"""F2 -- the foreman diagnoses and instructs; code validates; budgets stay rigid.

Owner rule: the foreman never decides a gate, never edits tests, gates, the
floor file or Factory code, never weakens a check, and never extends a budget.
Its instructions pass the same no-hardwiring lint as the brief, plus: an
instruction aimed at a probe id or a test file (a per-case branch), at a file
outside the WRITER's lane, or at a capability the findings did not name (a
widened ratchet) is refused before delivery. Two refusals fall back to the
raw typed findings. ``FACTORY_FOREMAN`` = off | shadow | on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import architect
from app.factory.build import brief_gates
from app.factory.build import foreman as fm
from app.factory.build import runner as runner_mod
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.findings import Finding, rework_targets
from app.factory.build.gates import GateResult
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import DECISION_REWORK, BuildBudget, Outcome, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

CAP_A, CAP_B, CAP_X = "zorblat_intake", "zorblat_ledger", "zorblat_unrelated"
CTX = fm.ForemanContext(
    capabilities=frozenset({CAP_A, CAP_B, CAP_X}),
    finding_capabilities=frozenset({CAP_A}),
    resolved_blocks=frozenset({"database"}),
    store_blocks=frozenset({"database", "event_bus"}),
    own_names=frozenset({"Zorblat Works", CAP_A, CAP_B, CAP_X}),
    known_literals=frozenset(),
)
FINDING = Finding(
    "FAILED tests/test_routes.py::test_x - payload refused",
    gate="TESTER",
    check_id="product_gate",
    capability_id=CAP_A,
    file="tests/test_routes.py",
    finding_shape="failure",
)


def _good(**over):
    out = {
        "mechanism": "the handler requires a field its model never declares",
        "affected_capability_ids": [CAP_A],
        "instructions": [
            {
                "file": "app/models.py",
                "what_to_build": "declare every field the handler reads as a required model field",
                "why": "a payload built from the model's own schema must be accepted",
                "capability_id": CAP_A,
            }
        ],
        "confidence": 0.6,
        "recommend": "continue",
        "stop_reason": None,
        "proposed_gates": [
            {
                "name": "model_declares_handler_inputs",
                "structural_rule": "every key a handler reads appears in its model FIELDS",
                "evidence": "the refused payload carried every declared field",
            }
        ],
    }
    out.update(over)
    return out


class _Llm:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _review(llm, *, mode="on", ctx=CTX, tmp_path=None, notes=None):
    return fm.review(
        [FINDING],
        ctx=ctx,
        gate="TESTER",
        check="product_gate",
        note=(lambda d, **kw: notes.append((d, kw))) if notes is not None else None,
        llm=llm,
        mode=mode,
        session_id="sess_abc123def",
        proposed_root=tmp_path,
    )


# -- schema --------------------------------------------------------------------


def test_a_well_formed_instruction_parses():
    ri, errors = fm.parse_instruction(_good())
    assert errors == [] and ri.affected_capability_ids == (CAP_A,)
    assert ri.instructions[0].file == "app/models.py" and ri.instructions[0].capability_id == CAP_A


@pytest.mark.parametrize(
    "over, why",
    [
        ({"mechanism": ""}, "mechanism"),
        ({"instructions": []}, "instructions"),
        ({"instructions": [{"file": "app/x.py", "why": "y"}]}, "file, what_to_build and why"),
        ({"confidence": 1.5}, "confidence"),
        ({"confidence": True}, "confidence"),
        ({"recommend": "maybe"}, "recommend"),
        ({"recommend": "stop", "stop_reason": None}, "stop_reason"),
        ({"affected_capability_ids": "zorblat_intake"}, "affected_capability_ids"),
        ({"proposed_gates": [{"name": "n"}]}, "proposed gate"),
    ],
)
def test_the_schema_refuses_a_malformed_instruction(over, why):
    ri, errors = fm.parse_instruction(_good(**over))
    assert ri is None and any(why in e for e in errors), errors


# -- validation ------------------------------------------------------------------


def _validate(**over):
    ri, errors = fm.parse_instruction(_good(**over))
    assert ri is not None, errors
    return fm.validate(ri, CTX)


def test_a_valid_instruction_passes_validation():
    assert _validate() == []


@pytest.mark.parametrize(
    "path, reason",
    [
        ("tests/test_zorblat_behaviour.py", "test file"),
        ("tests/test_routes.py", "Factory-rendered"),
        ("scripts/acceptance.py", "Factory-rendered"),
        ("scripts/factory_checks.py", "Factory-rendered"),
        (".github/workflows/ci.yml", "Factory-rendered"),
        ("../outside.py", "not a path inside the workspace"),
        ("/etc/passwd", "not a path inside the workspace"),
        ("backend/app/factory/acceptance_floor.v2.json", "outside the WRITER's lane"),
    ],
)
def test_an_instruction_outside_the_writers_lane_is_refused(path, reason):
    ins = dict(_good()["instructions"][0], file=path)
    errors = _validate(instructions=[ins])
    assert any(reason in e for e in errors), errors


def test_an_instruction_may_not_widen_the_ratchet():
    ins = dict(_good()["instructions"][0], capability_id=CAP_X)
    errors = _validate(affected_capability_ids=[CAP_A, CAP_X], instructions=[ins])
    assert any("widens the ratchet" in e for e in errors), errors
    errors = _validate(affected_capability_ids=["not_in_blueprint"])
    assert any("outside the blueprint" in e for e in errors), errors


def test_a_localised_round_needs_each_instruction_to_name_its_capability():
    ins = dict(_good()["instructions"][0], capability_id=None)
    errors = _validate(instructions=[ins])
    assert any("names no capability" in e for e in errors), errors


def test_the_lint_refuses_a_session_a_product_a_probe_and_a_test():
    known = frozenset({"other_product_capability"})
    ctx = fm.ForemanContext(**{**CTX.__dict__, "known_literals": known})
    probe = sorted(fm._probe_ids())[0]
    cases = {
        "cites a build session": "copy what sess_0a1b2c3d4e did",
        "another product": "reuse other_product_capability here",
        "probe or stage id": f"make {probe} pass",
        "points at a test file": "match what tests/test_routes.py expects",
    }
    for reason, text in cases.items():
        ins = dict(_good()["instructions"][0], what_to_build=text)
        ri, _ = fm.parse_instruction(_good(instructions=[ins]))
        errors = fm.validate(ri, ctx)
        assert any(reason in e for e in errors), (reason, errors)


# -- the entry point ---------------------------------------------------------------


def test_on_hands_the_writer_typed_instructions_that_keep_the_ratchet(tmp_path):
    notes = []
    result = _review(_Llm(_good()), tmp_path=tmp_path, notes=notes)
    assert result.used and not result.fallback and not result.stop
    (item,) = result.work
    assert isinstance(item, Finding) and item.capability_id == CAP_A and item.file == "app/models.py"
    assert rework_targets(result.work, [CAP_A, CAP_B, CAP_X]) == {CAP_A}


def test_two_refusals_fall_back_to_the_typed_findings(tmp_path):
    bad = _good(affected_capability_ids=[CAP_X])
    llm = _Llm(bad, bad)
    notes = []
    result = _review(llm, tmp_path=tmp_path, notes=notes)
    assert llm.calls == 2 and result.fallback and not result.used and result.work == ()
    assert [kw["architect"]["fallback"] for _, kw in notes] == [False, True]
    assert "fell back to the raw typed findings" in notes[-1][0]


def test_a_regeneration_after_one_refusal_is_used(tmp_path):
    llm = _Llm(_good(affected_capability_ids=[CAP_X]), _good())
    result = _review(llm, tmp_path=tmp_path)
    assert llm.calls == 2 and result.used and result.attempts == 2


def test_a_stop_is_honoured_only_at_or_above_the_confidence_bar(tmp_path):
    sure = _good(recommend="stop", stop_reason="the brief asks for an external system", confidence=0.85)
    unsure = dict(sure, confidence=0.5)
    honoured = _review(_Llm(sure), tmp_path=tmp_path)
    assert honoured.stop and honoured.stop_reason == "the brief asks for an external system"
    ignored = _review(_Llm(unsure), tmp_path=tmp_path)
    assert not ignored.stop and ignored.used


def test_shadow_calls_and_records_but_hands_nothing(tmp_path):
    notes = []
    result = _review(_Llm(_good()), mode="shadow", tmp_path=tmp_path, notes=notes)
    assert result.instruction is not None and not result.used and result.work == ()
    shadow_stop = _review(_Llm(_good(recommend="stop", stop_reason="r", confidence=0.99)), mode="shadow")
    assert not shadow_stop.stop
    assert notes


def test_off_makes_no_call():
    llm = _Llm(_good())
    result = _review(llm, mode="off")
    assert llm.calls == 0 and result.instruction is None and result.mode == "off"


def test_each_call_writes_one_ledger_entry_with_its_inputs_and_output(tmp_path, monkeypatch):
    monkeypatch.setattr(architect, "architect_model", lambda: "stub-model")
    notes = []
    reply = dict(_good(), _tokens=321)
    _review(_Llm(reply), tmp_path=tmp_path, notes=notes)
    (detail, kw) = notes[0]
    rec = kw["architect"]
    assert kw["stage"] == architect.LEDGER_STAGE and rec["entry"] == fm.ENTRY
    assert rec["model"] == "stub-model" and rec["tokens"] == 321
    assert len(rec["inputs_hash"]) == 64 and json.loads(rec["output"])["mechanism"]
    assert "_tokens" not in json.loads(rec["output"])


def test_proposed_gates_go_to_the_review_sink_never_a_verdict(tmp_path):
    _review(_Llm(_good()), tmp_path=tmp_path)
    path = tmp_path.joinpath(*fm.PROPOSED_GATES_DIR, "sess_abc123def.json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert rows[0]["name"] == "model_declares_handler_inputs" and rows[0]["source"] == fm.ENTRY


# -- the call site in the runner: budgets stay ceilings ------------------------------


def _run(tmp_path, monkeypatch, verdicts, *, mode, replies, max_rework=2):
    """Stub build: TESTER answers ``verdicts`` in order, then passes."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_AUTO_PILOT", raising=False)
    monkeypatch.setenv(fm.FLAG_ENV, mode)
    llm = _Llm(*replies)
    monkeypatch.setattr(architect, "_default_llm", lambda: llm)
    blueprint = load_blueprint(SMOKE)
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
                calls.append(tuple(ctx.work_list))
            return RoleResult(ok=True, detail="stub")

        return run

    monkeypatch.setattr(runner_mod, "gate_for", gate_for)
    runner = RoleRunner(
        blueprint,
        tmp_path / "build",
        roles={role: stub(role) for role in BUILD_PHASES},
        budget=BuildBudget(max_rework=max_rework),
    )
    return runner, runner.run(), calls, llm


def _tester_fail(runner_caps, shape):
    cap = runner_caps[0]
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


def _caps():
    return [c.id for c in load_blueprint(SMOKE).capabilities]


def _reply_for(cap, **over):
    ins = dict(_good()["instructions"][0], capability_id=cap)
    return _good(affected_capability_ids=[cap], instructions=[ins], **over)


def test_runner_off_and_shadow_hand_the_writer_the_same_work(tmp_path, monkeypatch):
    caps = _caps()
    off_runner, off_out, off_calls, off_llm = _run(
        tmp_path / "off", monkeypatch, [_tester_fail(caps, "failure")], mode="off", replies=[_reply_for(caps[0])]
    )
    sh_runner, sh_out, sh_calls, sh_llm = _run(
        tmp_path / "shadow", monkeypatch, [_tester_fail(caps, "failure")], mode="shadow", replies=[_reply_for(caps[0])]
    )
    assert off_llm.calls == 0 and sh_llm.calls >= 1
    assert off_out.outcome is Outcome.SUCCESS and sh_out.outcome is Outcome.SUCCESS
    assert [list(map(str, c)) for c in off_calls] == [list(map(str, c)) for c in sh_calls]


def test_runner_on_hands_instructions_and_a_confident_stop_ends_the_build(tmp_path, monkeypatch):
    caps = _caps()
    runner, out, calls, _ = _run(
        tmp_path / "on", monkeypatch, [_tester_fail(caps, "failure")], mode="on", replies=[_reply_for(caps[0])]
    )
    assert out.outcome is Outcome.SUCCESS
    handed = calls[1]
    assert any(isinstance(i, Finding) and i.gate == fm.ENTRY for i in handed)
    assert not any(isinstance(i, Finding) and i.gate == "TESTER" for i in handed)
    rework = next(e for e in runner.ledger.events() if (e.payload or {}).get("foreman"))
    assert rework.payload["foreman"]["used"] and rework.payload["typed_findings"]

    sure = _reply_for(caps[0], recommend="stop", stop_reason="cannot be built from the resolved blocks", confidence=0.9)
    runner, out, calls, _ = _run(tmp_path / "stop", monkeypatch, [_tester_fail(caps, "failure")], mode="on", replies=[sure])
    assert out.outcome is Outcome.FAILED_GATE and "foreman:cannot be built" in out.detail
    assert len(calls) == 1  # no rework round was dispatched


def test_the_foreman_cannot_extend_a_spent_budget(tmp_path, monkeypatch):
    caps = _caps()
    verdicts = [_tester_fail(caps, s) for s in ("first", "second", "third")]
    runner, out, calls, llm = _run(
        tmp_path / "budget", monkeypatch, verdicts, mode="on", replies=[_reply_for(caps[0])], max_rework=2
    )
    assert out.outcome is Outcome.FAILED_BUDGET_SPENT, out.detail
    assert len(calls) == 3  # first pass + exactly two reworks
    events = list(runner.ledger.events())
    reworks = [e for e in events if (e.payload or {}).get("decision", {}).get("class") == DECISION_REWORK]
    assert len(reworks) == 2
    # Every architect model call is one ledger NOTE naming its entry point.
    # The FOREMAN was consulted exactly once per rework round and never once
    # the budget was spent: no foreman call after the last REWORK.
    calls_by = [(i, (e.payload or {}).get("architect", {}).get("entry")) for i, e in enumerate(events)
                if (e.payload or {}).get("architect")]
    foreman = [i for i, entry in calls_by if entry == fm.ENTRY]
    assert len(foreman) == 2
    last_rework = max(i for i, e in enumerate(events) if e in reworks)
    assert all(i < last_rework for i in foreman), "the foreman was consulted after the budget was spent"
    # The other calls are the F5 failure narrative, which runs only after the
    # run has failed -- it explains the stop, it cannot extend anything.
    from app.factory.build import failure_narrative as fnar
    from app.factory.build.ledger import EventKind as _EK

    failed_at = max(i for i, e in enumerate(events) if e.kind is _EK.RUN_FAILED)
    others = [(i, entry) for i, entry in calls_by if entry != fm.ENTRY]
    assert all(entry == fnar.ENTRY and i > failed_at for i, entry in others)
    assert llm.calls == len(foreman) + len(others)
