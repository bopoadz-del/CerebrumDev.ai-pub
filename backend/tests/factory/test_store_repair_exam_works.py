"""F-1 (grader 21:50Z on #249): the repair exam must require the block to WORK.

Exam first. Before F-1 a block that validated its inputs and returned nothing
passed every sealed case, deleting the offending file passed the static case,
and a writer could pass by special-casing the one failing input it is handed.
These tests state what the exam must hold from the block's own contract and
its vendored surface only. Every block id, input, output and literal here is
invented.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.factory.build import store_repair_exam as sre
from app.factory.build.store_override import StoreDefect

_CONTRACT = {
    "id": "vorpal",
    "inputs": [
        {"name": "payload", "type": "json", "required": True},
        {"name": "mode", "type": "string", "required": False, "options": ["alpha", "beta"]},
        {"name": "limit", "type": "number", "required": False},
    ],
    "outputs": [{"name": "result", "type": "json"}],
    "acceptance": [{"id": "x1", "check": "refuses a missing input", "status": "refused"}],
}

_WORKS = (
    "LIMIT = 3\n"
    "def run(**kw):\n"
    "    if 'payload' not in kw:\n"
    "        return {'status': 'error'}\n"
    "    if 'mode' in kw and kw['mode'] not in ('alpha', 'beta'):\n"
    "        return {'status': 'error'}\n"
    "    return {'status': 'success', 'result': {'echo': kw['payload'], 'n': len(kw)}}\n"
)
_STUB = (
    "LIMIT = 3\n"
    "def run(**kw):\n"
    "    if 'payload' not in kw:\n"
    "        return {'status': 'error'}\n"
    "    if 'mode' in kw and kw['mode'] not in ('alpha', 'beta'):\n"
    "        return {'status': 'error'}\n"
    "    return {'status': 'success'}\n"
)
_BROKEN = "LIMIT = 3\ndef run(**kw):\n    raise RuntimeError('broken')\n"
_HELPER = "def helper(x):\n    return x\n"


def _vendor(tmp_path: Path, block_id: str = "vorpal", source: str = _BROKEN) -> Path:
    block = tmp_path / "vendor" / "blocks" / block_id
    block.mkdir(parents=True)
    (block / "block.json").write_text(json.dumps(_CONTRACT), encoding="utf-8")
    (block / "block.py").write_text(source, encoding="utf-8")
    (block / "helpers.py").write_text(_HELPER, encoding="utf-8")
    return block


def _invoker(staging: Path):
    spec = importlib.util.spec_from_file_location(f"f1_{staging.name}_{id(staging)}", staging / "block.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda call: module.run(**call)


def _runtime_defect(block_id: str = "vorpal"):
    return (StoreDefect(block_id, f"vendor/blocks/{block_id}/block.py", "crash", {"payload": {"q": 1}}),)


def _repair(tmp_path, writer, defects, rescan=lambda d: []):
    return sre.make_repairer(tmp_path, writer=writer, invoker=_invoker, rescan=rescan)("vorpal", defects)


def _write_block(source):
    def write(block_id, staging, view):
        (staging / "block.py").write_text(source, encoding="utf-8")

    return write


# --- the three the order names: a stub fails, a deleted file fails, a correct repair passes


@pytest.mark.parametrize(
    "stub",
    [
        _STUB,
        _STUB.replace("{'status': 'success'}", "{'status': 'success', 'result': {}}"),
        _STUB.replace("{'status': 'success'}", "{'status': 'success', 'result': None}"),
        _STUB.replace("{'status': 'success'}", "None"),
    ],
)
def test_a_stub_that_validates_and_returns_nothing_fails_the_exam(stub, tmp_path):
    _vendor(tmp_path)
    result = _repair(tmp_path, _write_block(stub), _runtime_defect())
    assert result.passed is False and result.exam_score < 1.0
    assert "empty" in result.detail


@pytest.mark.parametrize("gone", ["helpers.py", "block.json"])
def test_deleting_a_vendored_file_fails_the_exam(gone, tmp_path):
    _vendor(tmp_path)

    def writer(block_id, staging, view):
        (staging / "block.py").write_text(_WORKS, encoding="utf-8")
        (staging / gone).unlink()

    result = _repair(tmp_path, writer, _runtime_defect())
    assert result.passed is False
    assert f"file removed: {gone}" in result.detail


def test_deleting_the_file_a_static_defect_names_fails_the_exam(tmp_path):
    _vendor(tmp_path, source=_WORKS)
    defect = (StoreDefect("vorpal", "vendor/blocks/vorpal/helpers.py", "forbidden_literal"),)

    def writer(block_id, staging, view):
        (staging / "helpers.py").unlink()

    result = _repair(tmp_path, writer, defect)
    assert result.passed is False
    assert "survive:helpers.py: file removed: helpers.py" in result.detail


@pytest.mark.parametrize("defects", ["runtime", "static"])
def test_a_correct_repair_passes_the_exam(defects, tmp_path):
    if defects == "runtime":
        _vendor(tmp_path)
        chosen = _runtime_defect()

        def writer(block_id, staging, view):
            (staging / "block.py").write_text(_WORKS, encoding="utf-8")
    else:
        _vendor(tmp_path, source=_WORKS)
        chosen = (StoreDefect("vorpal", "vendor/blocks/vorpal/helpers.py", "forbidden_literal"),)

        def writer(block_id, staging, view):
            (staging / "helpers.py").write_text("def helper(x):\n    return [x]\n", encoding="utf-8")

    result = _repair(tmp_path, writer, chosen)
    assert (result.passed, result.exam_score, result.detail) == (True, 1.0, "")


def test_removing_an_exported_symbol_fails_the_exam(tmp_path):
    _vendor(tmp_path)

    def writer(block_id, staging, view):
        (staging / "block.py").write_text(_WORKS.replace("LIMIT = 3\n", ""), encoding="utf-8")

    result = _repair(tmp_path, writer, _runtime_defect())
    assert result.passed is False
    assert "exported symbols removed from block.py: LIMIT" in result.detail


# --- special-casing the failing input: the sealed half holds inputs the writer never sees


def test_special_casing_the_failing_input_fails_the_exam(tmp_path):
    _vendor(tmp_path)
    special = (
        "LIMIT = 3\n"
        "def run(**kw):\n"
        "    if 'payload' not in kw:\n"
        "        return {'status': 'error'}\n"
        "    if kw == {'payload': {'q': 1}}:\n"
        "        return {'status': 'success', 'result': {'ok': 1}}\n"
        "    if 'mode' in kw and kw['mode'] not in ('alpha', 'beta'):\n"
        "        return {'status': 'error'}\n"
        "    if 'q' in kw['payload']:\n"
        "        raise RuntimeError('still broken')\n"
        "    return {'status': 'success', 'result': {'ok': 1}}\n"
    )
    seen = []

    def writer(block_id, staging, view):
        seen.append(json.dumps(view, default=str))
        (staging / "block.py").write_text(special, encoding="utf-8")

    result = _repair(tmp_path, writer, _runtime_defect())
    assert result.passed is False
    assert "defect:0:variant:" in result.detail and "still fails: crash" in result.detail
    exam = sre.generate_exam("vorpal", _CONTRACT, _runtime_defect())
    variants = [c for c in exam.sealed if ":variant:" in c.case_id]
    assert variants
    for case in variants:
        assert case.case_id not in seen[0]
        assert json.dumps(case.inputs, sort_keys=True) not in seen[0]


@pytest.mark.parametrize(
    "call, want",
    [
        (
            {"payload": {"q": 1}},
            [
                {"payload": {"q": 1}, "mode": "alpha"},
                {"payload": {"q": 1}, "mode": "beta"},
                {"payload": {"q": 1}, "limit": 1},
            ],
        ),
        (
            {"mode": "beta"},
            [
                {"payload": {}, "mode": "beta"},
                {"mode": "alpha"},
                {"mode": "beta", "limit": 1},
            ],
        ),
        ({"input": 7}, [{"payload": {}, "input": 7}, {"input": 7, "mode": "alpha"}, {"input": 7, "mode": "beta"}, {"input": 7, "limit": 1}]),
    ],
)
def test_variants_of_a_failing_input_come_from_the_contract(call, want):
    assert sre._variants(call, _CONTRACT) == want


@pytest.mark.parametrize(
    "contract, call",
    [
        ({"inputs": [{"name": "k", "required": True, "options": ["a"]}]}, {"k": "a"}),
        ({"inputs": [{"name": "k", "required": True}]}, {"k": 5}),
    ],
)
def test_variants_never_repeat_the_failing_input(contract, call):
    assert sre._variants(call, contract) == []


@pytest.mark.parametrize("failing_input, count", [({"payload": {"q": 1}}, 3), (7, 4)])
def test_each_runtime_defect_carries_its_variants_into_the_sealed_half(failing_input, count):
    defect = StoreDefect("vorpal", "vendor/blocks/vorpal/block.py", "crash", failing_input)
    exam = sre.generate_exam("vorpal", _CONTRACT, [defect])
    defect_cases = [c for c in exam.sealed if c.source == "defect"]
    assert [c.case_id for c in defect_cases] == ["defect:0"] + [
        f"defect:0:variant:{k}" for k in range(count)
    ]
    assert {c.expect for c in defect_cases} == {sre.ACCEPTED}
    assert {c.kind for c in defect_cases} == {"crash"}
    assert {c.check for c in defect_cases} == {sre.RUNTIME}


# --- output and acceptance cases from the contract


@pytest.mark.parametrize(
    "outputs, result, why",
    [
        ([{"name": "result", "type": "json"}], {"status": "success", "result": {"a": 1}}, None),
        ([{"name": "result", "type": "json"}], {"a": 1}, None),
        ([{"name": "result", "type": "json"}], {"status": "success"}, "accepted with an empty result"),
        ([{"name": "result", "type": "json"}], {"ok": True}, "accepted with an empty result"),
        ([{"name": "result", "type": "json"}], None, "accepted with an empty result"),
        ([{"name": "result", "type": "json"}], [], "accepted with an empty result"),
        ([{"name": "result", "type": "json"}], "", "accepted with an empty result"),
        ([{"name": "result", "type": "json"}], {"result": {}}, "empty output: result"),
        ([{"name": "result", "type": "json"}], {"result": None, "x": 1}, "empty output: result"),
        ([{"name": "a", "type": "number"}, {"name": "b", "type": "string"}], {"a": 1, "b": "z"}, None),
        ([{"name": "a", "type": "number"}, {"name": "b", "type": "string"}], {"a": 1}, "missing output: b"),
        ([{"name": "a", "type": "number"}, {"name": "b", "type": "string"}], {"a": 1, "b": ""}, "empty output: b"),
        ([{"name": "a", "type": "number"}, {"name": "b", "type": "string"}], {"a": "1", "b": "z"}, "output a is not number"),
        ([{"name": "a", "type": "number"}], {"a": True}, "output a is not number"),
        ([{"name": "a", "type": "boolean"}], {"a": True}, None),
        ([{"name": "a", "type": "boolean"}], {"a": 1}, "output a is not boolean"),
        ([{"name": "a", "type": "array"}], {"a": [0]}, None),
        ([{"name": "a", "type": "array"}], {"a": {"k": 0}}, "output a is not array"),
        ([{"name": "a", "type": "object"}], {"a": [0]}, "output a is not object"),
        ([{"name": "a", "type": "mystery"}], {"a": 0}, None),
        ([{"name": "a", "type": "string"}], "text", None),
        ([{"type": "string"}, "junk"], {"z": 1}, None),
        ([], {"z": 1}, None),
        ([], {"status": "success"}, "accepted with an empty result"),
    ],
)
def test_an_accepted_call_must_deliver_the_declared_outputs(outputs, result, why):
    assert sre.output_failure(outputs, result) == why


@pytest.mark.parametrize("returned", [{"status": "success"}, {"status": "success", "result": {}}])
def test_an_empty_result_fails_the_contract_cases_by_run_exam(returned):
    exam = sre.generate_exam("vorpal", _CONTRACT, [])
    def stub(call):
        valid = "payload" in call and call.get("mode", "alpha") in ("alpha", "beta")
        return returned if valid else {"status": "error"}

    run = sre.run_exam(exam, stub, list)
    assert run.passed is False
    accepted = [c for c in exam.sealed if c.expect == sre.ACCEPTED]
    assert sorted(f.case_id for f in run.failures) == sorted(c.case_id for c in accepted)
    assert all("empty" in f.failure for f in run.failures)


@pytest.mark.parametrize("contract", [_CONTRACT, {"inputs": [{"name": "k", "required": True}]}])
def test_the_self_test_rejects_an_exam_that_cannot_catch_an_empty_result(contract):
    exam = sre.generate_exam("vorpal", contract, [])
    checked = sre.self_test(exam, contract)
    assert checked.ok and checked.caught["empty_result"] is True
    only_refusals = sre.Exam(exam.exam_id, "vorpal", exam.working, tuple(c for c in exam.sealed if c.expect == sre.REFUSED))
    degenerate = sre.self_test(only_refusals, contract)
    assert degenerate.ok is False and degenerate.caught["empty_result"] is False


@pytest.mark.parametrize(
    "outputs, want",
    [
        ([{"name": "a", "type": "number"}, {"name": "b", "type": "array"}], {"status": "success", "a": 1, "b": [1]}),
        ([{"name": "a", "type": "mystery"}], {"status": "success", "a": {"sample": 1}}),
        ([], {"status": "success", "result": {"sample": 1}}),
    ],
)
def test_the_contract_reference_produces_every_declared_output(outputs, want):
    contract = {"inputs": [], "outputs": outputs}
    assert sre.contract_reference(contract)({}) == want
    assert sre.contract_reference(contract, mutant="empty_result")({}) == {"status": "success"}


@pytest.mark.parametrize(
    "acceptance, status, refused",
    [
        ([{"status": "partial"}], "partial", True),
        ([{"status": "PARTIAL"}], "partial", True),
        ([], "partial", False),
        ([{"status": ""}, "junk"], "partial", False),
        ([], "failed", True),
    ],
)
def test_a_status_the_contract_declares_for_acceptance_is_a_refusal(acceptance, status, refused):
    contract = {"acceptance": acceptance}
    statuses = sre.refusal_statuses(contract)
    assert (status in statuses) is refused
    assert sre.REFUSAL_STATUSES <= statuses
    got = sre.outcome(lambda call: {"status": status, "result": 1}, {}, statuses)
    assert got == (sre.REFUSED if refused else sre.ACCEPTED)


def test_a_block_answering_a_declared_acceptance_status_fails_the_base_case():
    contract = {**_CONTRACT, "acceptance": [{"id": "x", "check": "c", "status": "partial"}]}
    exam = sre.generate_exam("vorpal", contract, [])
    run = sre.run_exam(exam, lambda call: {"status": "partial", "result": {"a": 1}}, list)
    assert "contract:base" in {f.case_id for f in run.failures}


@pytest.mark.parametrize(
    "declared, call",
    [
        ({"limit": -5}, {"payload": {}, "limit": -5}),
        ({"payload": {"z": 0}}, {"payload": {"z": 0}}),
    ],
)
def test_an_acceptance_entry_that_declares_an_input_is_a_sealed_refusal_case(declared, call):
    contract = {
        **_CONTRACT,
        "acceptance": [
            {"id": "x", "check": "c", "status": "refused", "input": "not a mapping"},
            {"id": "y", "check": "c", "status": "refused", "input": declared},
        ],
    }
    exam = sre.generate_exam("vorpal", contract, [])
    (case,) = [c for c in exam.sealed if c.source == "acceptance"]
    assert (case.case_id, case.check, case.expect) == ("acceptance:1", sre.RUNTIME, sre.REFUSED)
    assert case.inputs == call
    assert sre.self_test(exam, contract).ok
    run = sre.run_exam(exam, sre.contract_reference(contract), list)
    assert [f.case_id for f in run.failures] == ["acceptance:1"]
    assert run.failures[0].failure == "accepted a call the contract refuses"


# --- the surface: files and exported symbols


@pytest.mark.parametrize(
    "source, names",
    [
        ("def run():\n    pass\nclass Thing:\n    pass\nasync def go():\n    pass\n", ["Thing", "go", "run"]),
        ("A = 1\nB: int = 2\n_C = 3\nx, y = 1, 2\nD.e = 4\n", ["A", "B"]),
        ("def _hidden():\n    pass\nimport os\nif True:\n    Z = 1\n", []),
        ("def broken(:\n", []),
        ("\x00", []),
    ],
)
def test_exported_symbols_are_the_public_top_level_names(source, names):
    assert sre._exported(source) == names


@pytest.mark.parametrize("cache", ["pkg/__pycache__", "__pycache__"])
def test_the_block_surface_lists_files_and_symbols_and_skips_caches(cache, tmp_path):
    (tmp_path / cache).mkdir(parents=True)
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "block.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "pkg" / "util.py").write_text("K = 1\n", encoding="utf-8")
    (tmp_path / cache / "util.cpython-311.pyc").write_bytes(b"\x00")
    (tmp_path / "block.json").write_text('{"run": 1}', encoding="utf-8")
    assert sre.block_surface(tmp_path) == {
        "block.json": [],
        "block.py": ["run"],
        "pkg/util.py": ["K"],
    }


_STATIC_AND_RUNTIME = [
    StoreDefect("v", "vendor/blocks/v/core/x.py", "forbidden_literal"),
    StoreDefect("v", "vendor/blocks/v/b.py", "forbidden_literal"),
    StoreDefect("v", "vendor/blocks/v/c.py", "crash", {"payload": {}}),
]


@pytest.mark.parametrize(
    "surface, want",
    [
        (
            {"b.py": ["run", "run", "A"], "block.json": []},
            {
                "survive:b.py": {"file": "b.py", "symbols": ["A", "run"]},
                "survive:block.json": {"file": "block.json", "symbols": []},
                "survive:core/x.py": {"file": "core/x.py", "symbols": []},
            },
        ),
        (
            None,
            {
                "survive:b.py": {"file": "b.py", "symbols": []},
                "survive:core/x.py": {"file": "core/x.py", "symbols": []},
            },
        ),
    ],
)
def test_survive_cases_cover_the_surface_and_every_static_defect_file(surface, want):
    exam = sre.generate_exam("v", _CONTRACT, _STATIC_AND_RUNTIME, surface)
    survive = {c.case_id: c.inputs for c in exam.sealed if c.check == sre.SURVIVE}
    assert survive == want
    assert {c.source for c in exam.sealed if c.check == sre.SURVIVE} == {"regression"}
    assert {c.expect for c in exam.sealed if c.check == sre.SURVIVE} == {sre.ACCEPTED}


@pytest.mark.parametrize(
    "staged, failure",
    [
        ({"b.py": ["A", "run", "extra"]}, None),
        ({"b.py": ["run"]}, "exported symbols removed from b.py: A"),
        ({"b.py": []}, "exported symbols removed from b.py: A, run"),
        ({"other.py": ["A", "run"]}, "file removed: b.py"),
    ],
)
def test_a_surviving_file_keeps_every_exported_symbol(staged, failure):
    exam = sre.generate_exam("v", {"inputs": []}, [], {"b.py": ["run", "A"]})
    run = sre.run_exam(exam, sre.contract_reference({"inputs": []}), list, lambda: staged)
    fails = [f.failure for f in run.failures if f.case_id == "survive:b.py"]
    assert fails == ([] if failure is None else [failure])
    assert run.passed is (failure is None)


@pytest.mark.parametrize(
    "surface, failures",
    [
        ({"b.py": []}, ["file removed: b.py"]),
        ({"a.py": ["X"], "b.py": []}, ["file removed: a.py", "file removed: b.py"]),
    ],
)
def test_without_a_staged_surface_no_file_survives(surface, failures):
    exam = sre.generate_exam("v", {"inputs": []}, [], surface)
    run = sre.run_exam(exam, sre.contract_reference({"inputs": []}), list)
    assert [f.failure for f in run.failures] == failures


@pytest.mark.parametrize(
    "one, two",
    [
        ({"b.py": ["run"]}, {"b.py": ["run", "A"]}),
        ({"b.py": ["run"]}, {"c.py": ["run"]}),
    ],
)
def test_the_exam_id_binds_the_surface(one, two):
    first = sre.generate_exam("v", _CONTRACT, [], one)
    assert first.exam_id != sre.generate_exam("v", _CONTRACT, [], two).exam_id
    assert first.exam_id == sre.generate_exam("v", _CONTRACT, [], dict(one)).exam_id
