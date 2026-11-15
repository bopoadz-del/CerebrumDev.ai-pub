"""R2 (Advisor 12:13Z, spec K-3; order 18:36Z item A1): a Store block is
repaired under an exam generated from its own declared contract plus the
failing case.

Exam first: the cases below state what the exam must hold, from the contract
alone (each required input missing -> refused; each declared option ->
accepted; an undeclared value -> refused; each defect's failing case). Every
block id, input name, option and literal here is invented.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.factory.build import store_repair_exam as sre
from app.factory.build.store_override import StoreDefect

_ONE_REQUIRED = {
    "id": "zorblat",
    "inputs": [
        {"name": "payload", "type": "json", "required": True},
        {"name": "mode", "type": "string", "required": False, "options": ["alpha", "beta"]},
    ],
    "outputs": [{"name": "result", "type": "json"}],
    "acceptance": [{"id": "missing_required_input", "status": "refused"}],
}
_TWO_REQUIRED = {
    "id": "quxbar",
    "inputs": [
        {"name": "left", "type": "number", "required": True},
        {"name": "unit", "type": "string", "required": True, "options": ["m", "ft", "yd"]},
    ],
    "outputs": [{"name": "value", "type": "number"}],
}
_NOTHING_REQUIRED = {"id": "plinth", "inputs": [{"name": "note", "type": "string", "required": False}]}


def _ids(exam):
    return sorted(c.case_id for c in exam.sealed)


@pytest.mark.parametrize(
    "contract, want",
    [
        (
            _ONE_REQUIRED,
            [
                "contract:base",
                "contract:missing:payload",
                "contract:option:mode:0",
                "contract:option:mode:1",
                "contract:undeclared:mode",
            ],
        ),
        (
            _TWO_REQUIRED,
            [
                "contract:base",
                "contract:missing:left",
                "contract:missing:unit",
                "contract:option:unit:0",
                "contract:option:unit:1",
                "contract:option:unit:2",
                "contract:undeclared:unit",
            ],
        ),
    ],
)
def test_exam_cases_come_from_the_declared_contract(contract, want):
    exam = sre.generate_exam(contract["id"], contract, [])
    assert _ids(exam) == sorted(want)
    expects = {c.case_id: c.expect for c in exam.sealed}
    assert all(expects[i] == sre.REFUSED for i in want if ":missing:" in i or ":undeclared:" in i)
    assert all(expects[i] == sre.ACCEPTED for i in want if ":base" in i or ":option:" in i)


@pytest.mark.parametrize(
    "contract, name, first",
    [(_TWO_REQUIRED, "unit", "m"), ({"inputs": [{"name": "k", "required": True, "options": ["z", "y"]}]}, "k", "z")],
)
def test_base_call_uses_the_first_declared_option(contract, name, first):
    exam = sre.generate_exam("b", contract, [])
    base = next(c for c in exam.sealed if c.case_id == "contract:base")
    assert base.inputs[name] == first


@pytest.mark.parametrize(
    "spec, sample",
    [
        ({"name": "a", "type": "number"}, 1),
        ({"name": "a", "type": "json"}, {}),
        ({"name": "a", "type": "string", "default": "dflt"}, "dflt"),
        ({"name": "a", "type": "mystery"}, "sample"),
    ],
)
def test_sample_value_follows_the_declared_type(spec, sample):
    assert sre._sample(spec) == sample


@pytest.mark.parametrize(
    "defect, check, inputs",
    [
        (StoreDefect("zorblat", "vendor/blocks/zorblat/core/x.py", "forbidden_literal"), sre.STATIC, "core/x.py"),
        (StoreDefect("zorblat", "vendor/blocks/zorblat/x.py", "crash", {"payload": {"n": 3}}), sre.RUNTIME, {"payload": {"n": 3}}),
        (StoreDefect("zorblat", "vendor/blocks/zorblat/x.py", "crash", 7), sre.RUNTIME, {"input": 7}),
    ],
)
def test_the_failing_case_joins_the_sealed_part(defect, check, inputs):
    exam = sre.generate_exam("zorblat", _ONE_REQUIRED, [defect])
    case = next(c for c in exam.sealed if c.source == "defect")
    assert (case.check, case.inputs, case.kind) == (check, inputs, defect.kind)


@pytest.mark.parametrize(
    "result, want",
    [
        ({"status": "success"}, sre.ACCEPTED),
        ("plain", sre.ACCEPTED),
        ({"status": "ERROR"}, sre.REFUSED),
        ({"status": "refused"}, sre.REFUSED),
        ({"ok": False}, sre.REFUSED),
        (ValueError("no"), sre.REFUSED),
    ],
)
def test_outcome_reads_a_raise_or_error_status_as_refusal(result, want):
    def invoke(call):
        if isinstance(result, Exception):
            raise result
        return result

    assert sre.outcome(invoke, {}) == want


@pytest.mark.parametrize(
    "contract, mutants",
    [
        (_ONE_REQUIRED, ("refuse_all", "empty_result", "silent_default", "accept_undeclared")),
        (_NOTHING_REQUIRED, ("refuse_all", "empty_result")),
        (
            {"inputs": [{"name": "k", "required": False, "options": ["a"]}]},
            ("refuse_all", "empty_result", "accept_undeclared"),
        ),
    ],
)
def test_mutants_are_the_wrong_variants_the_contract_can_tell_apart(contract, mutants):
    assert sre.mutants_of(contract) == mutants


@pytest.mark.parametrize("contract", [_ONE_REQUIRED, _TWO_REQUIRED, _NOTHING_REQUIRED])
def test_generated_exam_passes_the_reference_and_fails_every_mutant(contract):
    checked = sre.self_test(sre.generate_exam("b", contract, []), contract)
    assert checked.ok, checked.reason
    assert sorted(checked.caught) == sorted(sre.mutants_of(contract))


@pytest.mark.parametrize("options", [["a", "b"], [None, "a"], [0, 1], ["", "undeclared:"]])
def test_the_undeclared_case_uses_a_value_no_declared_option_equals(options):
    contract = {"inputs": [{"name": "k", "required": True, "options": options}]}
    exam = sre.generate_exam("b", contract, [])
    case = next(c for c in exam.sealed if c.case_id == "contract:undeclared:k")
    assert case.inputs["k"] not in options
    assert sre.self_test(exam, contract).ok


@pytest.mark.parametrize(
    "keep, missed",
    [
        (lambda c: ":missing:" not in c.case_id, "silent_default"),
        (lambda c: ":undeclared:" not in c.case_id, "accept_undeclared"),
    ],
)
def test_an_exam_that_cannot_tell_a_mutant_apart_is_rejected(keep, missed):
    exam = sre.generate_exam("b", _ONE_REQUIRED, [])
    degenerate = sre.Exam(exam.exam_id, exam.block_id, exam.working, tuple(c for c in exam.sealed if keep(c)))
    checked = sre.self_test(degenerate, _ONE_REQUIRED)
    assert not checked.ok
    assert checked.caught[missed] is False
    assert missed in checked.reason


@pytest.mark.parametrize("flip", ["contract:base", "contract:missing:payload"])
def test_an_exam_the_contract_reference_fails_is_rejected(flip):
    exam = sre.generate_exam("b", _ONE_REQUIRED, [])
    wrong = tuple(
        sre.ExamCase(c.case_id, c.source, c.check, c.inputs, sre.REFUSED if c.expect == sre.ACCEPTED else sre.ACCEPTED)
        if c.case_id == flip
        else c
        for c in exam.sealed
    )
    checked = sre.self_test(sre.Exam(exam.exam_id, "b", exam.working, wrong), _ONE_REQUIRED)
    assert not checked.ok
    assert flip in checked.reason


def _no_findings():
    return []


@pytest.mark.parametrize("contract", [_ONE_REQUIRED, _TWO_REQUIRED])
def test_a_block_that_keeps_its_contract_passes_the_exam(contract):
    run = sre.run_exam(sre.generate_exam("b", contract, []), sre.contract_reference(contract), _no_findings)
    assert (run.passed, run.score, run.failures) == (True, 1.0, ())


@pytest.mark.parametrize(
    "mutant, failure",
    [
        ("silent_default", "accepted a call the contract refuses"),
        ("refuse_all", "refused a call the contract accepts"),
    ],
)
def test_a_failed_case_names_its_input_and_kind_never_the_expectation(mutant, failure):
    exam = sre.generate_exam("b", _ONE_REQUIRED, [])
    run = sre.run_exam(exam, sre.contract_reference(_ONE_REQUIRED, mutant=mutant), _no_findings)
    assert not run.passed and run.score < 1.0
    assert {f.failure for f in run.failures} == {failure}
    for f in run.failures:
        assert not hasattr(f, "expect")
        assert sre.ACCEPTED not in json.dumps(f.inputs) and sre.REFUSED not in json.dumps(f.inputs)


@pytest.mark.parametrize(
    "findings, passed",
    [
        ([{"file": "core/x.py", "kind": "forbidden_literal"}], False),
        ([{"file": "core\\x.py", "kind": "forbidden_literal"}], False),
        ([{"file": "core/other.py", "kind": "forbidden_literal"}], True),
        ([{"file": "core/x.py", "kind": "prompt_leak"}], True),
        ([], True),
    ],
)
def test_a_static_defect_passes_only_when_the_same_check_no_longer_finds_it(findings, passed):
    defect = StoreDefect("zorblat", "vendor/blocks/zorblat/core/x.py", "forbidden_literal")
    exam = sre.generate_exam("zorblat", _ONE_REQUIRED, [defect])
    run = sre.run_exam(exam, sre.contract_reference(_ONE_REQUIRED), lambda: findings, lambda: {"core/x.py": []})
    assert run.passed is passed
    if not passed:
        assert [f.failure for f in run.failures] == ["still found: forbidden_literal"]


@pytest.mark.parametrize("crashes, passed", [(True, False), (False, True)])
def test_a_runtime_defect_passes_only_when_its_failing_input_is_accepted(crashes, passed):
    defect = StoreDefect("zorblat", "vendor/blocks/zorblat/x.py", "crash", {"payload": {"n": -1}})
    reference = sre.contract_reference(_ONE_REQUIRED)

    def invoke(call):
        if crashes and call == {"payload": {"n": -1}}:
            raise ZeroDivisionError("boom")
        return reference(call)

    run = sre.run_exam(sre.generate_exam("zorblat", _ONE_REQUIRED, [defect]), invoke, _no_findings)
    assert run.passed is passed
    assert [f.failure for f in run.failures] == ([] if passed else ["still fails: crash"])


@pytest.mark.parametrize("cases", [(), ("drop",)])
def test_an_empty_exam_never_passes(cases):
    exam = sre.generate_exam("b", _ONE_REQUIRED, [])
    empty = sre.Exam(exam.exam_id, "b", exam.working, tuple(c for c in exam.sealed if c.case_id in cases))
    run = sre.run_exam(empty, sre.contract_reference(_ONE_REQUIRED), _no_findings)
    assert (run.passed, run.score) == (False, 0.0)


@pytest.mark.parametrize("contract", [_ONE_REQUIRED, _TWO_REQUIRED])
def test_the_writer_view_holds_no_sealed_case_or_expectation(contract, tmp_path):
    defect = StoreDefect(contract["id"], f"vendor/blocks/{contract['id']}/x.py", "crash", {"q": 1})
    exam = sre.generate_exam(contract["id"], contract, [defect])
    view = json.dumps(sre.handback(exam, [defect], tmp_path), default=str)
    for case in exam.sealed:
        assert case.case_id not in view
    assert '"expect"' not in view and '"sealed"' not in view
    assert sorted(json.loads(view)) == ["block_id", "contract", "defects", "exam_id", "source_dir"]
    assert json.loads(view)["defects"] == [{"path": defect.path, "kind": "crash", "failing_input": {"q": 1}}]
    assert json.loads(view)["contract"]["inputs"] == contract["inputs"]


# --- the repairer: staging copy, writer round, exam on the staged repair ----

_BROKEN = "def run(**kw):\n    raise RuntimeError('broken')\n"
_FIXED = (
    "def run(**kw):\n"
    "    if 'payload' not in kw:\n"
    "        return {'status': 'error'}\n"
    "    if 'mode' in kw and kw['mode'] not in ('alpha', 'beta'):\n"
    "        return {'status': 'error'}\n"
    "    return {'status': 'success', 'result': dict(kw)}\n"
)


def _vendor(tmp_path: Path, block_id: str, contract=_ONE_REQUIRED, source=_BROKEN) -> Path:
    block = tmp_path / "vendor" / "blocks" / block_id
    block.mkdir(parents=True)
    if contract is not None:
        (block / "block.json").write_text(json.dumps(contract), encoding="utf-8")
    (block / "block.py").write_text(source, encoding="utf-8")
    return block


def _invoker(staging: Path):
    spec = importlib.util.spec_from_file_location(f"staged_{staging.name}", staging / "block.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda call: module.run(**call)


def _writer(source, calls):
    def write(block_id, staging, view):
        calls.append((block_id, staging, view))
        (staging / "block.py").write_text(source, encoding="utf-8")

    return write


def _defect(block_id):
    return (StoreDefect(block_id, f"vendor/blocks/{block_id}/block.py", "crash", {"payload": {}}),)


@pytest.mark.parametrize("block_id", ["zorblat", "quxbar"])
def test_a_repair_that_passes_its_exam_is_reported_repaired(block_id, tmp_path):
    vendored = _vendor(tmp_path, block_id)
    before = sre.tree_hash(vendored)
    calls = []
    repair = sre.make_repairer(tmp_path, writer=_writer(_FIXED, calls), invoker=_invoker, rescan=lambda d: [])
    result = repair(block_id, _defect(block_id))
    assert (result.passed, result.exam_score, result.detail) == (True, 1.0, "")
    assert result.exam_id.startswith(f"exam:{block_id}:")
    assert calls[0][1] == tmp_path / "repair" / block_id
    assert sre.tree_hash(vendored) == before


@pytest.mark.parametrize("source", [_BROKEN, "def run(**kw):\n    return {'status': 'success'}\n"])
def test_a_repair_that_fails_its_exam_is_reported_not_repaired(source, tmp_path):
    _vendor(tmp_path, "zorblat")
    repair = sre.make_repairer(tmp_path, writer=_writer(source, []), invoker=_invoker, rescan=lambda d: [])
    result = repair("zorblat", _defect("zorblat"))
    assert result.passed is False and result.exam_score < 1.0
    assert result.detail != ""


@pytest.mark.parametrize("block_id", ["zorblat", "quxbar"])
def test_a_repair_that_touches_the_vendored_copy_fails(block_id, tmp_path):
    vendored = _vendor(tmp_path, block_id)

    def writer(bid, staging, view):
        (staging / "block.py").write_text(_FIXED, encoding="utf-8")
        (vendored / "block.py").write_text(_FIXED, encoding="utf-8")

    result = sre.make_repairer(tmp_path, writer=writer, invoker=_invoker, rescan=lambda d: [])(block_id, _defect(block_id))
    assert (result.passed, result.detail) == (False, "the repair changed the vendored copy")


@pytest.mark.parametrize("block_id", ["zorblat", "quxbar"])
def test_a_block_without_a_declared_contract_is_not_repaired(block_id, tmp_path):
    _vendor(tmp_path, block_id, contract=None)
    calls = []
    repair = sre.make_repairer(tmp_path, writer=_writer(_FIXED, calls), invoker=_invoker, rescan=lambda d: [])
    result = repair(block_id, _defect(block_id))
    assert result.passed is False and calls == []
    assert result.detail.startswith("no declared contract")


@pytest.mark.parametrize("dropped", [":missing:", ":undeclared:"])
def test_an_exam_defect_stops_the_repair_before_the_writer_and_names_the_factory(dropped, tmp_path, monkeypatch):
    _vendor(tmp_path, "zorblat")
    real = sre.generate_exam

    def degenerate(block_id, contract, defects, surface=None):
        exam = real(block_id, contract, defects, surface)
        return sre.Exam(exam.exam_id, block_id, exam.working, tuple(c for c in exam.sealed if dropped not in c.case_id))

    monkeypatch.setattr(sre, "generate_exam", degenerate)
    calls = []
    result = sre.make_repairer(tmp_path, writer=_writer(_FIXED, calls), invoker=_invoker, rescan=lambda d: [])(
        "zorblat", _defect("zorblat")
    )
    assert result.passed is False and calls == []
    assert result.detail.startswith("exam defect (Factory):")


@pytest.mark.parametrize("stale", ["old.py", "nested/old.py"])
def test_a_stale_staging_copy_is_replaced_by_the_vendored_one(stale, tmp_path):
    _vendor(tmp_path, "zorblat")
    leftover = tmp_path / "repair" / "zorblat" / stale
    leftover.parent.mkdir(parents=True)
    leftover.write_text("x = 1\n", encoding="utf-8")
    sre.make_repairer(tmp_path, writer=_writer(_FIXED, []), invoker=_invoker, rescan=lambda d: [])(
        "zorblat", _defect("zorblat")
    )
    assert not leftover.exists()
    assert (tmp_path / "repair" / "zorblat" / "block.json").is_file()


@pytest.mark.parametrize("block_id", ["zorblat", "quxbar"])
def test_the_repairer_settles_a_store_only_verdict_through_k2(block_id, tmp_path):
    """The repairer is the one K-2's settle() calls: a passed repair delivers."""
    from types import SimpleNamespace

    from app.factory.build import grader as grader_mod
    from app.factory.build import store_repair

    _vendor(tmp_path, block_id)
    finding = {"file": f"vendor/blocks/{block_id}/block.py", "kind": "crash", "input": {"payload": {}}, "owner": "FACTORY"}
    owed = (f"FACTORY owes {finding['file']}",)
    verdict = grader_mod.GraderVerdict(
        tier=grader_mod.NOT_CERTIFIED,
        items=owed,
        grade={"hardwiring": {"findings": [finding]}},
        factory_owed=owed,
    )
    repair = sre.make_repairer(tmp_path, writer=_writer(_FIXED, []), invoker=_invoker, rescan=lambda d: [])
    caps = [SimpleNamespace(capability_id="cap_a", block_ids=[block_id])]
    (row,) = store_repair.settle(verdict, plan_capabilities=caps, events=[], repairer=repair)
    assert (row.repaired, row.not_delivered, row.exam_score) == (True, (), 1.0)
